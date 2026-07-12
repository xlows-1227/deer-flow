"""DraftService — structured authoring source-of-truth for published agents.

The service is the single place that writes drafts, used by both the Gateway
Studio endpoints and the conversational ``setup_agent`` / ``update_agent``
tools. It composes three collaborators:

- ``PublishedAgentRepository`` / ``AgentDraftRepository`` (F1.1 persistence)
- a ``SkillsIndex`` — a tiny protocol so the service can validate that a
  selected skill is either public or owned by the agent owner, without the
  harness depending on any specific skill loader implementation
- a connector repository (duck-typed ``get_instance``), used to confirm a
  granted connector instance still belongs to the owner

All methods are owner-scoped: a cross-owner call returns ``None`` / raises a
conflict rather than leaking another tenant's data. Saving a draft never
touches ``current_release_id`` — that pointer only moves via the publish
service.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from deerflow.persistence.published_agent import (
    AgentDraftRepository,
    PublishedAgentRepository,
)


class DraftConflictError(Exception):
    """Raised when a draft update is attempted with a stale revision (409)."""


class SkillNotSelectableError(Exception):
    """Raised when a selected skill is unknown or not owned by the caller (422)."""


class ConnectorNotGrantableError(Exception):
    """Raised when a granted connector instance does not belong to the caller (422)."""


class SkillsIndex(Protocol):
    """Minimal interface the service needs to authorize skill selection.

    ``get`` returns the authoritative visibility/owner classification for a
    skill so the service never trusts a client-supplied ``source`` (code-review
    Important-1). It returns ``None`` for unknown skills.
    """

    def is_selectable_by(self, name: str, owner_user_id: str) -> bool: ...

    def get(self, name: str) -> dict[str, Any] | None: ...


def _resolve_skill_source(skills_index: SkillsIndex, name: str, owner_user_id: str) -> str:
    """Derive the authoritative ``source`` (public|private) for a skill.

    Never trusts a client-supplied value: the platform owns the classification
    (code-review Important-1). Falls back to ``public`` only when the index has
    no metadata (legacy indexes), since ``is_selectable_by`` already gated
    ownership.
    """
    info = skills_index.get(name)
    if info is None:
        return "public"
    return "private" if info.get("visibility") == "private" else "public"


class ConnectorRepoLike(Protocol):
    """Subset of the connector repository the service calls."""

    async def get_instance(self, connector_id: str, *, owner_id: Any = ...) -> dict[str, Any] | None: ...


class DraftService:
    def __init__(
        self,
        *,
        published_agent_repo: PublishedAgentRepository,
        draft_repo: AgentDraftRepository,
        skills_index: SkillsIndex,
        connector_repo: ConnectorRepoLike,
    ) -> None:
        self._agents = published_agent_repo
        self._drafts = draft_repo
        self._skills = skills_index
        self._connectors = connector_repo

    # ------------------------------------------------------------------
    # agent identity + draft reads
    # ------------------------------------------------------------------

    async def create_agent(
        self,
        *,
        owner_user_id: str,
        slug: str,
        display_name: str,
        description: str | None = None,
        avatar_ref: str | None = None,
    ) -> dict[str, Any]:
        return await self._agents.create_agent(
            owner_user_id=owner_user_id,
            slug=slug,
            display_name=display_name,
            description=description,
            avatar_ref=avatar_ref,
        )

    async def get_agent(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        return await self._agents.get(agent_id, owner_user_id=owner_user_id)

    async def list_agents(self, owner_user_id: str) -> list[dict[str, Any]]:
        return await self._agents.list_by_owner(owner_user_id)

    async def get_draft(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        return await self._drafts.get(agent_id, owner_user_id=owner_user_id)

    async def update_agent_meta(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        display_name: str | None = None,
        description: str | None = None,
        avatar_ref: str | None = None,
    ) -> dict[str, Any] | None:
        """Update agent identity metadata (display name / description / avatar).

        Description is identity-level (not draft-level), so conversational tools
        that change it mirror through here (rereview Important-5). Returns the
        updated agent or ``None`` if not found / not owned.
        """
        return await self._agents.update_meta(
            agent_id,
            owner_user_id=owner_user_id,
            display_name=display_name,
            description=description,
            avatar_ref=avatar_ref,
        )

    # ------------------------------------------------------------------
    # draft updates
    # ------------------------------------------------------------------

    async def update_draft(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        revision: int,
        agent_markdown: str | None = None,
        soul_markdown: str | None = None,
        model_name: str | None = None,
        tool_groups: Sequence[str] | None = None,
        quota_overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        updated = await self._drafts.update_with_revision(
            agent_id,
            owner_user_id=owner_user_id,
            revision=revision,
            agent_markdown=agent_markdown,
            soul_markdown=soul_markdown,
            model_name=model_name,
            tool_groups=tool_groups,
            quota_overrides=quota_overrides,
        )
        if updated is None:
            # Either the draft doesn't belong to the caller, or the revision
            # was stale. Both surface as the same 409 to avoid leaking whether
            # the resource exists.
            raise DraftConflictError("draft revision conflict or not found")
        return updated

    async def update_draft_bundle(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        revision: int,
        agent_markdown: str | None = None,
        soul_markdown: str | None = None,
        model_name: str | None = None,
        tool_groups: Sequence[str] | None = None,
        quota_overrides: Mapping[str, Any] | None = None,
        skills: Sequence[Mapping[str, str]] | None = None,
        connector_grants: Sequence[Mapping[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Atomically update the draft main row and its sub-tables (Critical-3).

        Skills and connector grants are validated *before* any write, then the
        main row + sub-tables are committed in a single transaction gated by the
        ``revision`` check. A stale revision means nothing is written — unlike
        the previous flow which called ``set_skills`` / ``set_connector_grants``
        before the revision-checked ``update_draft``, leaving sub-tables mutated
        on a 409.
        """
        # Pre-validate skills and connectors before touching the DB so a 422 is
        # raised without any write, and a 409 leaves everything unchanged.
        # The skill ``source`` is derived authoritatively from the index, never
        # from the client-supplied value (code-review Important-1).
        resolved_skills: list[Mapping[str, str]] | None = None
        if skills is not None:
            resolved_skills = []
            for entry in skills:
                name = str(entry["skill_name"])
                if not self._skills.is_selectable_by(name, owner_user_id):
                    raise SkillNotSelectableError(f"skill not selectable: {name}")
                resolved_skills.append({"skill_name": name, "source": _resolve_skill_source(self._skills, name, owner_user_id)})
        if connector_grants is not None:
            for entry in connector_grants:
                instance_id = str(entry["connector_instance_id"])
                instance = await self._connectors.get_instance(instance_id, owner_id=owner_user_id)
                if instance is None:
                    raise ConnectorNotGrantableError(f"connector not grantable: {instance_id}")
        updated = await self._drafts.update_bundle(
            agent_id,
            owner_user_id=owner_user_id,
            revision=revision,
            agent_markdown=agent_markdown,
            soul_markdown=soul_markdown,
            model_name=model_name,
            tool_groups=tool_groups,
            quota_overrides=quota_overrides,
            skills=resolved_skills,
            connector_grants=connector_grants,
        )
        if updated is None:
            raise DraftConflictError("draft revision conflict or not found")
        return updated

    async def set_skills(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        skills: Sequence[Mapping[str, str]],
    ) -> dict[str, Any]:
        resolved: list[Mapping[str, str]] = []
        for entry in skills:
            name = str(entry["skill_name"])
            if not self._skills.is_selectable_by(name, owner_user_id):
                raise SkillNotSelectableError(f"skill not selectable: {name}")
            resolved.append({"skill_name": name, "source": _resolve_skill_source(self._skills, name, owner_user_id)})
        result = await self._drafts.replace_skills(agent_id, owner_user_id=owner_user_id, skills=resolved)
        if result is None:
            raise DraftConflictError("draft not found")
        return result

    async def set_connector_grants(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        grants: Sequence[Mapping[str, str]],
    ) -> dict[str, Any]:
        for entry in grants:
            instance_id = str(entry["connector_instance_id"])
            instance = await self._connectors.get_instance(instance_id, owner_id=owner_user_id)
            if instance is None:
                raise ConnectorNotGrantableError(f"connector not grantable: {instance_id}")
        result = await self._drafts.replace_connector_grants(agent_id, owner_user_id=owner_user_id, grants=grants)
        if result is None:
            raise DraftConflictError("draft not found")
        return result

    # ------------------------------------------------------------------
    # lifecycle (suspend / resume / archive) — never delete data
    # ------------------------------------------------------------------

    async def suspend(self, agent_id: str, *, owner_user_id: str) -> bool:
        return await self._agents.set_status(agent_id, owner_user_id=owner_user_id, status="suspended")

    async def resume(self, agent_id: str, *, owner_user_id: str) -> bool:
        return await self._agents.set_status(agent_id, owner_user_id=owner_user_id, status="published")

    async def archive(self, agent_id: str, *, owner_user_id: str) -> bool:
        return await self._agents.set_status(agent_id, owner_user_id=owner_user_id, status="archived")
