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
from deerflow.publishing.skills_index import SkillPublishSnapshot
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


class _SnapshotSkillsIndex:
    """Validator adapter backed only by captured publish snapshots."""

    def __init__(self, snapshots: dict[str, SkillPublishSnapshot | None]) -> None:
        self._snapshots = snapshots

    def is_selectable_by(self, name: str, owner_user_id: str) -> bool:  # noqa: ARG002
        return self._snapshots.get(name) is not None

    def get(self, name: str) -> dict[str, Any] | None:
        snapshot = self._snapshots.get(name)
        return snapshot.validation_info() if snapshot is not None else None


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
        model_resolver: Any = None,
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
        # Session factory shared by all repos — used to open a single transaction
        # spanning skill-revision upserts + release creation + pointer switch
        # (fourth-review Important-1).
        self._sf = getattr(skill_revision_repo, "_sf", None)
        # Optional async resolver ``(owner_user_id) -> set[str]`` that returns the
        # owner's effective model names (config + user-defined). When provided it
        # is called per-publish so user model enable/disable and hot-reload are
        # honoured (third-review Important-2). Falls back to the static
        # ``model_index`` when None (tests / CLI).
        self._model_resolver = model_resolver

    async def _build_sync_connector_repo(self, draft: dict[str, Any], owner_user_id: str) -> Any:
        """Resolve connector availability and type capabilities for validation.

        The validator is a pure function and cannot ``await``; this helper
        eagerly asks the async connector repo for owner-active instances plus
        their authoritative type capability sets, then returns a tiny sync
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

    def _resolve_skill_snapshots(
        self,
        draft: dict[str, Any],
        *,
        owner_user_id: str,
    ) -> tuple[dict[str, Any], dict[str, SkillPublishSnapshot | None]]:
        """Capture the only Skill state validation and revision writes may use."""
        resolver = getattr(self._skills, "resolve_publish_snapshots", None)
        if resolver is None:
            raise PublishError(
                [
                    PublishViolation(
                        "SKILL_INDEX_UNAVAILABLE",
                        "Cannot resolve immutable Skill snapshots for this draft.",
                    )
                ]
            )
        inherit = draft.get("skill_selection_mode", "explicit") == "inherit"
        selected_names = None if inherit else [entry["skill_name"] for entry in draft.get("skills") or []]
        snapshots = resolver(selected_names, owner_user_id)
        effective = dict(draft)
        if inherit:
            effective["skills"] = [
                {
                    "skill_name": name,
                    "source": snapshot.source if snapshot is not None else "unresolved",
                }
                for name, snapshot in snapshots.items()
            ]
        return effective, snapshots

    async def publish(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any]:
        get_snapshot = getattr(self._drafts, "get_publish_snapshot", self._drafts.get)
        draft = await get_snapshot(agent_id, owner_user_id=owner_user_id)
        if draft is None:
            raise PublishError([PublishViolation("AGENT_NOT_FOUND", "Agent not found.")])
        draft, skill_snapshots = self._resolve_skill_snapshots(
            draft,
            owner_user_id=owner_user_id,
        )
        # Pre-resolve async connector ownership into a sync adapter so the
        # pure validator stays synchronous and easily testable.
        sync_connector_repo = await self._build_sync_connector_repo(draft, owner_user_id)
        # Resolve the owner's effective model set per-publish when a resolver is
        # configured, so user-defined models and hot-reload are honoured
        # (third-review Important-2).
        if self._model_resolver is not None:
            effective_models = await self._model_resolver(owner_user_id)
        else:
            effective_models = self._model_index
        violations = validate_draft_for_publish(
            draft,
            owner_user_id=owner_user_id,
            skills_index=_SnapshotSkillsIndex(skill_snapshots),
            connector_repo=sync_connector_repo,
            model_index=effective_models,
            tool_group_whitelist=self._tool_group_whitelist,
            platform_quota=self._platform_quota,
        )
        if violations:
            raise PublishError(violations)

        # Pre-compute skill checksums / content refs / visibility (pure, no DB).
        prepared_skills: list[dict[str, Any]] = []
        for entry in draft.get("skills") or []:
            name = entry["skill_name"]
            snapshot = skill_snapshots.get(name)
            if snapshot is None:
                # The validator above always emits SKILL_NOT_FOUND first.
                raise AssertionError(f"validated Skill snapshot missing: {name}")
            files = snapshot.file_map()
            checksum = _skill_checksum(name, files)
            prepared_skills.append(
                {
                    "skill_name": name,
                    "owner_user_id": snapshot.owner_user_id,
                    "visibility": snapshot.visibility,
                    "content_checksum": checksum,
                    "declared_connector_caps": list(snapshot.declared_connector_caps),
                    "files": files,
                }
            )

        from sqlalchemy.exc import IntegrityError

        # Complete publish unit-of-work: skill-revision upserts + release row +
        # sub-tables + pointer switch all commit (or roll back) together
        # (fourth-review Important-1). On a (agent_id, release_no) unique
        # conflict the whole transaction is retried with a fresh release_no.
        release_no = await self._releases.next_release_no(agent_id)
        release: dict[str, Any] | None = None
        for _attempt in range(8):
            try:
                release = await self._publish_unit_of_work(
                    agent_id=agent_id,
                    owner_user_id=owner_user_id,
                    draft=draft,
                    prepared_skills=prepared_skills,
                    release_no=release_no,
                )
                break
            except IntegrityError as exc:
                msg = str(getattr(exc, "orig", "") or exc).lower()
                if "release_no" not in msg and "uq_agent_releases_agent_release_no" not in msg:
                    raise
                release_no = await self._releases.next_release_no(agent_id)
        if release is None:
            raise PublishError([PublishViolation("RELEASE_RACE", "Could not allocate a release number after retries.")])
        return {"release_id": release["id"], "release_no": release_no, "published_at": release["created_at"]}

    async def _publish_unit_of_work(
        self,
        *,
        agent_id: str,
        owner_user_id: str,
        draft: dict[str, Any],
        prepared_skills: list[dict[str, Any]],
        release_no: int,
    ) -> dict[str, Any]:
        """Run skill-revision upserts + release create + pointer switch in ONE
        transaction. If this raises, nothing is committed — no orphan revisions
        or releases (fourth-review Important-1).
        """
        if self._sf is None:
            # Fallback (tests without a shared factory): do it in two steps.
            latest = await self._drafts.get(agent_id, owner_user_id=owner_user_id)
            if latest is None:
                raise PublishError([PublishViolation("AGENT_NOT_FOUND", "Agent not found.")])
            if latest.get("revision") != draft.get("revision"):
                raise PublishError(
                    [
                        PublishViolation(
                            "DRAFT_REVISION_CONFLICT",
                            "Draft changed while it was being published; retry with the latest revision.",
                        )
                    ]
                )
            skill_revision_ids: list[str] = []
            skill_links: list[dict[str, str]] = []
            for ps in prepared_skills:
                revision_values = self._store_skill_snapshot(ps)
                rev = await self._skill_revs.get_or_create(**revision_values)
                skill_revision_ids.append(rev["id"])
                skill_links.append({"skill_revision_id": rev["id"]})
            return await self._releases.create_and_point(
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

        # Single shared session: skill revisions + release + pointer.

        async with self._sf() as session:
            async with session.begin():
                revision_matches = await self._drafts.lock_revision_for_publish(
                    session,
                    agent_id,
                    owner_user_id=owner_user_id,
                    expected_revision=int(draft["revision"]),
                )
                if revision_matches is None:
                    raise PublishError([PublishViolation("AGENT_NOT_FOUND", "Agent not found.")])
                if not revision_matches:
                    raise PublishError(
                        [
                            PublishViolation(
                                "DRAFT_REVISION_CONFLICT",
                                "Draft changed while it was being published; retry with the latest revision.",
                            )
                        ]
                    )
                skill_revision_ids = []
                skill_links = []
                for ps in prepared_skills:
                    revision_values = self._store_skill_snapshot(ps)
                    row = await self._skill_revs._get_or_create_in_session(session, **revision_values)  # noqa: SLF001
                    skill_revision_ids.append(row.id)
                    skill_links.append({"skill_revision_id": row.id})
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
                    session=session,
                )
            return release

    def _store_skill_snapshot(self, prepared: dict[str, Any]) -> dict[str, Any]:
        """Persist captured bytes and return immutable revision column values."""
        values = dict(prepared)
        files = values.pop("files")
        checksum = values["content_checksum"]
        storage_key = checksum.split(":", 1)[1] if ":" in checksum else checksum
        values["content_ref"] = self._content.put(
            namespace="skills",
            checksum=storage_key,
            files=files,
        )
        return values

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
