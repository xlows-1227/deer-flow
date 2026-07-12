"""PublishService — validate, snapshot, and atomically publish agent drafts.

The publish flow (design doc §6.1 / §8.2) runs inside a single conceptual
transaction:

1. load the draft (owner-scoped);
2. run :func:`validate_draft_for_publish` and, on any violation, raise
   :class:`PublishError` carrying every violation — the agent's state is
   left completely unchanged;
3. for each selected skill, compute its content checksum and upsert an
   immutable :class:`SkillRevisionRow` (reused if the content already exists)
   plus a content-store snapshot;
4. insert a write-once :class:`AgentReleaseRow` with its skill / connector
   sub-table rows;
5. atomically flip ``published_agents.current_release_id`` to the new release
   and set ``status='published'``.

Rollback never touches release history — it only repoints the current-release
pointer, so historical releases stay byte-identical and auditable.
"""

from __future__ import annotations

import hashlib
from typing import Any

from deerflow.persistence.agent_release import AgentReleaseRepository
from deerflow.persistence.published_agent import (
    AgentDraftRepository,
    PublishedAgentRepository,
)
from deerflow.persistence.skill_revision import SkillRevisionRepository
from deerflow.publishing.content_store import ImmutableContentStore
from deerflow.publishing.validation import (
    PLATFORM_QUOTA_DEFAULTS,
    PublishViolation,
    validate_draft_for_publish,
)


class PublishError(Exception):
    """Raised when a draft fails validation. Carries every violation."""

    def __init__(self, violations: list[PublishViolation]) -> None:
        self.violations = violations
        super().__init__("; ".join(v.message for v in violations))


class ReleaseNotFoundError(Exception):
    """Raised when a rollback target release_no does not exist for the owner."""


def _skill_checksum(skill_name: str, files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    h.update(skill_name.encode())
    h.update(b"\0")
    for name in sorted(files):
        h.update(name.encode())
        h.update(b"\0")
        h.update(files[name])
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


def _manifest_checksum(draft: dict[str, Any], skill_revision_ids: list[str]) -> str:
    h = hashlib.sha256()
    h.update((draft.get("agent_markdown") or "").encode())
    h.update(b"\0")
    h.update((draft.get("soul_markdown") or "").encode())
    h.update(b"\0")
    h.update((draft.get("model_name") or "").encode())
    h.update(b"\0")
    for group in sorted(draft.get("tool_groups") or []):
        h.update(group.encode())
        h.update(b"\0")
    for grant in sorted((g["connector_instance_id"], g["capability"]) for g in draft.get("connector_grants") or []):
        h.update(f"{grant[0]}:{grant[1]}".encode())
        h.update(b"\0")
    for rid in sorted(skill_revision_ids):
        h.update(rid.encode())
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


class PublishService:
    def __init__(
        self,
        *,
        published_agent_repo: PublishedAgentRepository,
        draft_repo: AgentDraftRepository,
        release_repo: AgentReleaseRepository,
        skill_revision_repo: SkillRevisionRepository,
        content_store: ImmutableContentStore,
        skills_index: Any,
        connector_repo: Any,
        model_index: set[str],
        tool_group_whitelist: set[str],
        platform_quota: dict[str, int] | None = None,
    ) -> None:
        self._agents = published_agent_repo
        self._drafts = draft_repo
        self._releases = release_repo
        self._skill_revs = skill_revision_repo
        self._content = content_store
        self._skills = skills_index
        self._connectors = connector_repo
        self._model_index = model_index
        self._tool_group_whitelist = tool_group_whitelist
        self._platform_quota = platform_quota or dict(PLATFORM_QUOTA_DEFAULTS)

    async def _build_sync_connector_repo(self, draft: dict[str, Any], owner_user_id: str) -> Any:
        """Resolve connector ownership synchronously for the validator.

        The validator is a pure function and cannot ``await``; this helper
        eagerly asks the (possibly async) connector repo which of the draft's
        granted instances the owner actually controls, then returns a tiny sync
        adapter answering from that pre-resolved map. Connectors not referenced
        by the draft are never queried.
        """
        resolved: dict[str, dict[str, Any] | None] = {}
        for grant in draft.get("connector_grants") or []:
            cid = grant["connector_instance_id"]
            if cid not in resolved:
                resolved[cid] = await self._connectors.get_instance(cid, owner_id=owner_user_id)

        class _SyncAdapter:
            def get_instance(self, connector_id: str, *, owner_id=...):  # noqa: ARG002
                return resolved.get(connector_id)

        return _SyncAdapter()

    # ------------------------------------------------------------------
    # publish
    # ------------------------------------------------------------------

    async def publish(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any]:
        draft = await self._drafts.get(agent_id, owner_user_id=owner_user_id)
        if draft is None:
            raise PublishError([PublishViolation("AGENT_NOT_FOUND", "Agent not found.")])
        # Pre-resolve async connector ownership into a sync adapter so the
        # pure validator stays synchronous and easily testable.
        sync_connector_repo = await self._build_sync_connector_repo(draft, owner_user_id)
        violations = validate_draft_for_publish(
            draft,
            owner_user_id=owner_user_id,
            skills_index=self._skills,
            connector_repo=sync_connector_repo,
            model_index=self._model_index,
            tool_group_whitelist=self._tool_group_whitelist,
            platform_quota=self._platform_quota,
        )
        if violations:
            raise PublishError(violations)

        # Pin each selected skill to an immutable revision.
        skill_revision_ids: list[str] = []
        skill_links: list[dict[str, str]] = []
        for entry in draft.get("skills") or []:
            name = entry["skill_name"]
            files = self._skills.files_for(name) if hasattr(self._skills, "files_for") else {b"SKILL.md": b""}
            checksum = _skill_checksum(name, files)
            # The content-store path is derived from the checksum; strip the
            # algorithm prefix so the hex digest is a safe directory name on
            # every platform (Windows rejects ':' in paths).
            storage_key = checksum.split(":", 1)[1] if ":" in checksum else checksum
            content_ref = self._content.put(namespace="skills", checksum=storage_key, files=files)
            info = self._skills.get(name) if hasattr(self._skills, "get") else None
            declared_caps = (info or {}).get("caps", []) if isinstance(info, dict) else []
            # Visibility/ownership are derived authoritatively from the skills
            # index, never from the draft's client-supplied ``source`` (code-
            # review Important-1).
            visibility = (info or {}).get("visibility", "public") if isinstance(info, dict) else "public"
            rev_owner = owner_user_id if visibility == "private" else None
            rev = await self._skill_revs.get_or_create(
                skill_name=name,
                owner_user_id=rev_owner,
                visibility=visibility,
                content_checksum=checksum,
                content_ref=content_ref,
                declared_connector_caps=declared_caps,
            )
            skill_revision_ids.append(rev["id"])
            skill_links.append({"skill_revision_id": rev["id"]})

        # Insert the immutable release row AND flip current_release_id in a
        # single transaction (rereview Important-1): ``create_and_point`` commits
        # the release row, its sub-tables, and the pointer update together, so a
        # failure cannot leave an orphan release or a rolled-back pointer.
        # Two concurrent publishes may both compute the same ``release_no`` from
        # MAX(release_no)+1; the (agent_id, release_no) unique constraint turns
        # that race into an IntegrityError on the loser, which we retry with a
        # fresh release_no (code-review Important-2). The skill-revision content
        # snapshots above are already idempotent on content checksum.
        from sqlalchemy.exc import IntegrityError

        release_no = await self._releases.next_release_no(agent_id)
        release: dict[str, Any] | None = None
        for _attempt in range(8):
            try:
                release = await self._releases.create_and_point(
                    {
                        "agent_id": agent_id,
                        "release_no": release_no,
                        "agent_markdown": draft.get("agent_markdown") or "",
                        "soul_markdown": draft.get("soul_markdown") or "",
                        "model_name": draft.get("model_name"),
                        "tool_groups": draft.get("tool_groups") or [],
                        "quota_overrides": draft.get("quota_overrides") or {},
                        "manifest_checksum": _manifest_checksum(draft, skill_revision_ids),
                        "created_by": owner_user_id,
                    },
                    owner_user_id=owner_user_id,
                    skills=skill_links,
                    connector_grants=draft.get("connector_grants") or [],
                )
                break
            except IntegrityError:
                release_no = await self._releases.next_release_no(agent_id)
        if release is None:
            raise PublishError([PublishViolation("RELEASE_RACE", "Could not allocate a release number after retries.")])
        return {"release_id": release["id"], "release_no": release_no, "published_at": release["created_at"]}

    # ------------------------------------------------------------------
    # rollback
    # ------------------------------------------------------------------

    async def rollback(self, agent_id: str, *, owner_user_id: str, release_no: int) -> dict[str, Any]:
        release = await self._releases.get_by_release_no(agent_id, release_no=release_no, owner_user_id=owner_user_id)
        if release is None:
            raise ReleaseNotFoundError(f"release {release_no} not found for agent {agent_id}")
        await self._agents.set_current_release(agent_id, owner_user_id=owner_user_id, release_id=release["id"])
        return {"release_id": release["id"], "release_no": release_no}

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_releases(self, agent_id: str, *, owner_user_id: str) -> list[dict[str, Any]]:
        return await self._releases.list_by_agent(agent_id, owner_user_id=owner_user_id)

    async def get_release(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        release_no: int,
    ) -> dict[str, Any] | None:
        return await self._releases.get_by_release_no(agent_id, release_no=release_no, owner_user_id=owner_user_id)
