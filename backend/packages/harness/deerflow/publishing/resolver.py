"""Resolve a stable published Agent identity into trusted runtime authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol

from deerflow.publishing.context import PublishedAgentContext, PublishedSkillMetadata
from deerflow.publishing.instructions import compose_published_agent_instructions
from deerflow.skills.parser import parse_allowed_tools, parse_skill_frontmatter


class AgentNotAvailableError(LookupError):
    """The stable id does not currently resolve to a runnable release."""


class AgentSuspendedError(RuntimeError):
    """The Agent exists but its lifecycle state forbids new runs."""


def _optional_frontmatter_text(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip() or None


class PublishedAgentRepoLike(Protocol):
    """Owner-scoped stable-Agent lookup required by the resolver."""

    async def get_owner(self, agent_id: str) -> str | None: ...

    async def get(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any] | None: ...


class AgentReleaseRepoLike(Protocol):
    """Immutable Release lookup required by the resolver."""

    async def get(self, release_id: str, *, owner_user_id: str) -> dict[str, Any] | None: ...


class ConnectorRepoLike(Protocol):
    """Authority-enriched active Connector lookup required by the resolver."""

    async def get_instance(self, connector_id: str, *, owner_id: str) -> dict[str, Any] | None: ...


class QuotaResolverLike(Protocol):
    """Credential-aware effective-quota lookup required by the resolver."""

    async def resolve(
        self,
        *,
        owner_user_id: str,
        release: dict[str, Any],
        credential_id: str,
    ) -> Any: ...


class SkillRevisionRepoLike(Protocol):
    """Immutable Skill revision lookup required by the resolver."""

    async def get(self, revision_id: str, *, owner_user_id: str) -> dict[str, Any] | None: ...


class ContentStoreLike(Protocol):
    """Read access to immutable Skill revision snapshots."""

    def get(self, content_ref: str) -> dict[str, bytes]: ...


class PublishedAgentResolver:
    """Load the current immutable release and derive least-privilege context."""

    def __init__(
        self,
        *,
        agent_repo: PublishedAgentRepoLike,
        release_repo: AgentReleaseRepoLike,
        connector_repo: ConnectorRepoLike,
        quota_resolver: QuotaResolverLike,
        skill_revision_repo: SkillRevisionRepoLike,
        content_store: ContentStoreLike,
    ) -> None:
        self._agents = agent_repo
        self._releases = release_repo
        self._connectors = connector_repo
        self._quotas = quota_resolver
        self._skill_revisions = skill_revision_repo
        self._content = content_store

    async def resolve(
        self,
        agent_id: str,
        *,
        source: Literal["api", "feishu"],
        credential_id: str,
        external_actor: str,
        conversation_scope: str,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> PublishedAgentContext:
        """Resolve a stable Agent id into one frozen, least-privilege context."""
        if source not in {"api", "feishu"}:
            raise ValueError("published agent source must be api or feishu")

        owner_user_id = await self._agents.get_owner(agent_id)
        if owner_user_id is None:
            raise AgentNotAvailableError(agent_id)
        agent = await self._agents.get(agent_id, owner_user_id=owner_user_id)
        if agent is None:
            raise AgentNotAvailableError(agent_id)

        status = str(agent.get("status") or "")
        if status in {"suspended", "archived"}:
            raise AgentSuspendedError(agent_id)
        release_id = agent.get("current_release_id")
        if status != "published" or not isinstance(release_id, str) or not release_id:
            raise AgentNotAvailableError(agent_id)

        release = await self._releases.get(release_id, owner_user_id=owner_user_id)
        if release is None or release.get("agent_id") != agent_id:
            raise AgentNotAvailableError(agent_id)

        connector_capabilities = await self._active_connector_capabilities(
            owner_user_id=owner_user_id,
            release=release,
        )
        skill_revision_ids = tuple(sorted(str(item["skill_revision_id"]) for item in release.get("skills") or [] if item.get("skill_revision_id")))
        allowed_tool_names, frozen_skills, skill_metadata = await self._frozen_skill_policy(
            skill_revision_ids,
            owner_user_id=owner_user_id,
        )
        effective_quota = await self._quotas.resolve(
            owner_user_id=owner_user_id,
            release=release,
            credential_id=credential_id,
        )
        instructions = compose_published_agent_instructions(
            str(release.get("agent_markdown") or ""),
            str(release.get("soul_markdown") or ""),
            frozen_skills,
        )
        model_name = release.get("model_name")
        if not isinstance(model_name, str) or not model_name:
            raise AgentNotAvailableError(agent_id)

        return PublishedAgentContext(
            owner_user_id=owner_user_id,
            agent_id=agent_id,
            release_id=release_id,
            source=source,
            credential_id=credential_id,
            external_actor=external_actor,
            conversation_scope=conversation_scope,
            skill_revision_ids=skill_revision_ids,
            connector_capabilities=connector_capabilities,
            tool_groups=tuple(str(group) for group in release.get("tool_groups") or []),
            model_name=model_name,
            instructions=instructions,
            effective_quota=effective_quota,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            skill_metadata=skill_metadata,
            allowed_tool_names=allowed_tool_names,
        )

    async def _frozen_skill_policy(
        self,
        revision_ids: tuple[str, ...],
        *,
        owner_user_id: str,
    ) -> tuple[
        tuple[str, ...] | None,
        tuple[tuple[str, str], ...],
        tuple[PublishedSkillMetadata, ...],
    ]:
        """Load pinned Skill bodies and derive their exact tool whitelist."""
        allowed: set[str] = set()
        frozen_skills: list[tuple[str, str]] = []
        skill_metadata: list[PublishedSkillMetadata] = []
        has_explicit_declaration = False
        for revision_id in revision_ids:
            revision = await self._skill_revisions.get(revision_id, owner_user_id=owner_user_id)
            if revision is None:
                raise AgentNotAvailableError(f"missing skill revision {revision_id}")
            owner_scope = revision.get("owner_scope")
            revision_owner = revision.get("owner_user_id")
            visibility = revision.get("visibility")
            is_public = owner_scope == "public" and revision_owner is None and visibility == "public"
            is_owned = owner_scope == owner_user_id and revision_owner == owner_user_id
            if not is_public and not is_owned:
                raise AgentNotAvailableError(f"out-of-scope skill revision {revision_id}")
            skill_name = revision.get("skill_name")
            content_ref = revision.get("content_ref")
            if not isinstance(skill_name, str) or not skill_name or not isinstance(content_ref, str) or not content_ref:
                raise AgentNotAvailableError(f"invalid skill revision {revision_id}")
            try:
                files = self._content.get(content_ref)
                skill_md = files["SKILL.md"].decode("utf-8")
                metadata = parse_skill_frontmatter(skill_md, Path(skill_name) / "SKILL.md")
                if metadata.get("name") != skill_name:
                    raise ValueError("skill metadata mismatch")
                description = _optional_frontmatter_text(metadata, "description")
                if description is None:
                    raise ValueError("skill description is required")
                display_name = _optional_frontmatter_text(metadata, "display_name") or skill_name
                public_description = _optional_frontmatter_text(metadata, "description_zh") or description
                declared = parse_allowed_tools(metadata.get("allowed-tools"), Path(skill_name) / "SKILL.md")
            except (KeyError, UnicodeDecodeError, ValueError) as exc:
                raise AgentNotAvailableError(f"unreadable skill revision {revision_id}") from exc
            if declared is not None:
                has_explicit_declaration = True
                allowed.update(declared)
            frozen_skills.append((skill_name, skill_md))
            skill_metadata.append(
                PublishedSkillMetadata(
                    name=skill_name,
                    display_name=display_name,
                    description=public_description,
                )
            )
        return (
            tuple(sorted(allowed)) if has_explicit_declaration else None,
            tuple(sorted(frozen_skills)),
            tuple(sorted(skill_metadata, key=lambda item: item.name)),
        )

    async def _active_connector_capabilities(
        self,
        *,
        owner_user_id: str,
        release: dict[str, Any],
    ) -> tuple[tuple[str, str], ...]:
        active: list[tuple[str, str]] = []
        for grant in release.get("connector_grants") or []:
            connector_id = str(grant.get("connector_instance_id") or "")
            capability = str(grant.get("capability") or "")
            if not connector_id or not capability:
                continue
            instance = await self._connectors.get_instance(connector_id, owner_id=owner_user_id)
            if instance is None:
                continue
            if str(instance.get("status") or "").lower() != "active":
                continue
            supported = instance.get("supported_capabilities")
            if not isinstance(supported, (list, tuple, set, frozenset)) or capability not in supported:
                continue
            active.append((connector_id, capability))
        return tuple(sorted(set(active)))
