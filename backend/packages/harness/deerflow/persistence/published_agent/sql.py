"""Async repositories for the published-agent control plane tables.

Every method takes an ``owner_user_id`` and filters on it so that a cross-owner
read never returns another tenant's data. Reads return plain ``dict`` values
(via ``_to_dict`` helpers that rename the ``_json``-suffixed columns); writes
commit and refresh within the same session.

The draft repository implements optimistic concurrency through
``update_with_revision``: the caller supplies the ``revision`` it last read and
the update is a conditional ``WHERE revision = :expected`` — if zero rows are
updated the method returns ``None`` so the caller can surface a 409.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.published_agent.model import (
    AgentDraftConnectorGrantRow,
    AgentDraftRow,
    AgentDraftSkillRow,
    PublishedAgentRow,
)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Row -> dict serializers
# ---------------------------------------------------------------------------


def _agent_to_dict(row: PublishedAgentRow) -> dict[str, Any]:
    return row.to_dict()


def _draft_to_dict(
    row: AgentDraftRow,
    *,
    skills: list[dict[str, str]] | None = None,
    connector_grants: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    data = row.to_dict()
    data["tool_groups"] = list(data.pop("tool_groups_json") or [])
    data["quota_overrides"] = dict(data.pop("quota_overrides_json") or {})
    data["skills"] = skills or []
    data["connector_grants"] = connector_grants or []
    return data


def _skill_to_dict(row: AgentDraftSkillRow) -> dict[str, str]:
    return {"skill_name": row.skill_name, "source": row.source}


def _grant_to_dict(row: AgentDraftConnectorGrantRow) -> dict[str, str]:
    return {"connector_instance_id": row.connector_instance_id, "capability": row.capability}


def _draft_from_joined_rows(rows: Sequence[Any]) -> dict[str, Any] | None:
    """Assemble one draft bundle returned by a single joined SELECT."""
    if not rows:
        return None
    _agent, draft, _skill, _grant = rows[0]
    skills = {row_skill.skill_name: _skill_to_dict(row_skill) for _row_agent, _row_draft, row_skill, _row_grant in rows if row_skill is not None}
    grants = {(row_grant.connector_instance_id, row_grant.capability): _grant_to_dict(row_grant) for _row_agent, _row_draft, _row_skill, row_grant in rows if row_grant is not None}
    return _draft_to_dict(
        draft,
        skills=[skills[name] for name in sorted(skills)],
        connector_grants=[grants[key] for key in sorted(grants)],
    )


async def _load_skill_dicts(session: AsyncSession, agent_id: str) -> list[dict[str, str]]:
    rows = (await session.execute(select(AgentDraftSkillRow).where(AgentDraftSkillRow.agent_id == agent_id).order_by(AgentDraftSkillRow.skill_name))).scalars().all()
    return [_skill_to_dict(row) for row in rows]


async def _load_grant_dicts(session: AsyncSession, agent_id: str) -> list[dict[str, str]]:
    rows = (
        (
            await session.execute(
                select(AgentDraftConnectorGrantRow)
                .where(AgentDraftConnectorGrantRow.agent_id == agent_id)
                .order_by(
                    AgentDraftConnectorGrantRow.connector_instance_id,
                    AgentDraftConnectorGrantRow.capability,
                )
            )
        )
        .scalars()
        .all()
    )
    return [_grant_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# PublishedAgentRepository
# ---------------------------------------------------------------------------


class PublishedAgentRepository:
    """Owner-scoped CRUD for the stable ``published_agents`` identity table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        self._after_authoring_identity_lock = None
        self._after_authoring_draft_lock = None
        self._after_import_draft_flush = None
        self._after_import_skills_flush = None

    async def setup_authoring_bundle(
        self,
        *,
        owner_user_id: str,
        slug: str,
        display_name: str,
        description: str | None,
        soul_markdown: str,
        skills: Sequence[Mapping[str, str]] | None,
        skill_selection_mode: str = "explicit",
    ) -> dict[str, Any]:
        """Create-or-update identity, draft and skills in one transaction."""
        async with self._sf() as session:
            try:
                agent = (await session.execute(select(PublishedAgentRow).where(PublishedAgentRow.owner_user_id == owner_user_id, PublishedAgentRow.slug == slug).with_for_update())).scalar_one_or_none()
                if agent is None:
                    agent = PublishedAgentRow(
                        id=f"pa_{uuid4().hex}",
                        owner_user_id=owner_user_id,
                        slug=slug,
                        display_name=display_name,
                        description=description,
                        status="draft",
                    )
                    session.add(agent)
                    draft = AgentDraftRow(
                        agent_id=agent.id,
                        soul_markdown=soul_markdown,
                        skill_selection_mode=skill_selection_mode,
                        updated_by=owner_user_id,
                    )
                    session.add(draft)
                    await session.flush()
                else:
                    agent.display_name = display_name
                    agent.description = description
                    agent.updated_at = _now()
                    if self._after_authoring_identity_lock is not None:
                        await self._after_authoring_identity_lock()
                    draft = await session.get(AgentDraftRow, agent.id, with_for_update=True)
                    if draft is None:
                        raise RuntimeError("draft not found")
                    if self._after_authoring_draft_lock is not None:
                        await self._after_authoring_draft_lock()
                    draft.soul_markdown = soul_markdown
                    draft.skill_selection_mode = skill_selection_mode
                    draft.revision += 1
                    draft.updated_by = owner_user_id
                    draft.updated_at = _now()
                if skills is not None:
                    await session.execute(delete(AgentDraftSkillRow).where(AgentDraftSkillRow.agent_id == agent.id))
                    for entry in skills:
                        session.add(AgentDraftSkillRow(agent_id=agent.id, skill_name=str(entry["skill_name"]), source=str(entry["source"])))
                await session.flush()
                saved = {
                    "agent": _agent_to_dict(agent),
                    "draft": _draft_to_dict(
                        draft,
                        skills=await _load_skill_dicts(session, agent.id),
                        connector_grants=await _load_grant_dicts(session, agent.id),
                    ),
                }
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
            return saved

    async def import_authoring_bundle(
        self,
        *,
        owner_user_id: str,
        slug: str,
        display_name: str,
        description: str | None,
        soul_markdown: str,
        model_name: str | None,
        tool_groups: Sequence[str],
        skills: Sequence[Mapping[str, str]],
        skill_selection_mode: str,
    ) -> dict[str, Any]:
        """Create one legacy-import identity, draft and Skills atomically."""
        agent = PublishedAgentRow(
            id=f"pa_{uuid4().hex}",
            owner_user_id=owner_user_id,
            slug=slug,
            display_name=display_name,
            description=description,
            status="draft",
        )
        draft = AgentDraftRow(
            agent_id=agent.id,
            soul_markdown=soul_markdown,
            model_name=model_name,
            tool_groups_json=list(tool_groups),
            skill_selection_mode=skill_selection_mode,
            updated_by=owner_user_id,
        )
        async with self._sf() as session:
            try:
                session.add(agent)
                session.add(draft)
                await session.flush()
                if self._after_import_draft_flush is not None:
                    await self._after_import_draft_flush()
                for entry in skills:
                    session.add(
                        AgentDraftSkillRow(
                            agent_id=agent.id,
                            skill_name=str(entry["skill_name"]),
                            source=str(entry["source"]),
                        )
                    )
                await session.flush()
                if self._after_import_skills_flush is not None:
                    await self._after_import_skills_flush()
                saved = {
                    "agent": _agent_to_dict(agent),
                    "draft": _draft_to_dict(
                        draft,
                        skills=await _load_skill_dicts(session, agent.id),
                        connector_grants=[],
                    ),
                }
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                message = str(getattr(exc, "orig", "") or exc).lower()
                if "uq_published_agents_owner_slug" in message or ("published_agents.owner_user_id" in message and "published_agents.slug" in message):
                    raise ValueError(f"Agent slug already exists for owner: {slug}") from exc
                raise
            except BaseException:
                await session.rollback()
                raise
        return saved

    async def update_authoring_bundle(
        self,
        *,
        owner_user_id: str,
        slug: str,
        description: str | None = None,
        soul_markdown: str | None = None,
        model_name: str | None = None,
        tool_groups: Sequence[str] | None = None,
        skills: Sequence[Mapping[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        """Update metadata, draft fields and skills with one commit/revision."""
        async with self._sf() as session:
            try:
                agent = (await session.execute(select(PublishedAgentRow).where(PublishedAgentRow.owner_user_id == owner_user_id, PublishedAgentRow.slug == slug).with_for_update())).scalar_one_or_none()
                if agent is None:
                    return None
                if self._after_authoring_identity_lock is not None:
                    await self._after_authoring_identity_lock()
                draft = await session.get(AgentDraftRow, agent.id, with_for_update=True)
                if draft is None:
                    return None
                if description is not None:
                    agent.description = description
                    agent.updated_at = _now()
                if soul_markdown is not None:
                    draft.soul_markdown = soul_markdown
                if model_name is not None:
                    draft.model_name = model_name
                if tool_groups is not None:
                    draft.tool_groups_json = list(tool_groups)
                if skills is not None:
                    draft.skill_selection_mode = "explicit"
                    await session.execute(delete(AgentDraftSkillRow).where(AgentDraftSkillRow.agent_id == agent.id))
                    for entry in skills:
                        session.add(AgentDraftSkillRow(agent_id=agent.id, skill_name=str(entry["skill_name"]), source=str(entry["source"])))
                draft.revision += 1
                draft.updated_by = owner_user_id
                draft.updated_at = _now()
                await session.flush()
                saved = {
                    "agent": _agent_to_dict(agent),
                    "draft": _draft_to_dict(
                        draft,
                        skills=await _load_skill_dicts(session, agent.id),
                        connector_grants=await _load_grant_dicts(session, agent.id),
                    ),
                }
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
            return saved

    async def create_agent(
        self,
        *,
        owner_user_id: str,
        slug: str,
        display_name: str,
        description: str | None = None,
        avatar_ref: str | None = None,
        agent_id: str | None = None,
        skill_selection_mode: str = "explicit",
    ) -> dict[str, Any]:
        row = PublishedAgentRow(
            id=str(agent_id or f"pa_{uuid4().hex}"),
            owner_user_id=str(owner_user_id),
            slug=str(slug),
            display_name=str(display_name),
            description=description,
            avatar_ref=avatar_ref,
            status="draft",
        )
        draft = AgentDraftRow(
            agent_id=row.id,
            skill_selection_mode=skill_selection_mode,
            updated_by=str(owner_user_id),
        )
        async with self._sf() as session:
            session.add(row)
            session.add(draft)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                # Re-raise as a plain ValueError so callers don't need to import
                # SQLAlchemy's exception type; the unique constraint on
                # (owner_user_id, slug) is the expected cause.
                raise ValueError(f"Agent slug already exists for owner: {slug}") from exc
            await session.refresh(row)
            return _agent_to_dict(row)

    async def get(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        stmt = select(PublishedAgentRow).where(
            PublishedAgentRow.id == agent_id,
            PublishedAgentRow.owner_user_id == owner_user_id,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _agent_to_dict(row) if row else None

    async def get_authoring_state(
        self,
        *,
        owner_user_id: str,
        slug: str,
    ) -> dict[str, Any] | None:
        """Load identity, draft, skills and grants from one SQL snapshot."""
        stmt = (
            select(
                PublishedAgentRow,
                AgentDraftRow,
                AgentDraftSkillRow,
                AgentDraftConnectorGrantRow,
            )
            .join(AgentDraftRow, AgentDraftRow.agent_id == PublishedAgentRow.id)
            .outerjoin(
                AgentDraftSkillRow,
                AgentDraftSkillRow.agent_id == AgentDraftRow.agent_id,
            )
            .outerjoin(
                AgentDraftConnectorGrantRow,
                AgentDraftConnectorGrantRow.agent_id == AgentDraftRow.agent_id,
            )
            .where(
                PublishedAgentRow.owner_user_id == owner_user_id,
                PublishedAgentRow.slug == slug,
            )
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).all()
            if not rows:
                return None
            agent, _draft, _skill, _grant = rows[0]
            return {
                "agent": _agent_to_dict(agent),
                "draft": _draft_from_joined_rows(rows),
            }

    async def list_by_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        stmt = select(PublishedAgentRow).where(PublishedAgentRow.owner_user_id == owner_user_id).order_by(PublishedAgentRow.created_at.desc())
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_agent_to_dict(row) for row in rows]

    async def update_meta(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        display_name: str | None = None,
        description: str | None = None,
        avatar_ref: str | None = None,
        slug: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(PublishedAgentRow).where(
                        PublishedAgentRow.id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if display_name is not None:
                row.display_name = display_name
            if description is not None:
                row.description = description
            if avatar_ref is not None:
                row.avatar_ref = avatar_ref
            if slug is not None:
                row.slug = slug
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _agent_to_dict(row)

    async def set_status(self, agent_id: str, *, owner_user_id: str, status: str) -> bool:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(PublishedAgentRow).where(
                        PublishedAgentRow.id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            row.status = status
            row.updated_at = _now()
            await session.commit()
            return True

    async def set_current_release(self, agent_id: str, *, owner_user_id: str, release_id: str | None) -> bool:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(PublishedAgentRow).where(
                        PublishedAgentRow.id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            row.current_release_id = release_id
            if release_id is not None and row.status == "draft":
                # First publish flips draft -> published.
                row.status = "published"
            row.updated_at = _now()
            await session.commit()
            return True

    async def get_owner(self, agent_id: str) -> str | None:
        """Return the owner of an agent without owner filtering (internal use)."""
        async with self._sf() as session:
            row = await session.get(PublishedAgentRow, agent_id)
            return row.owner_user_id if row else None


# ---------------------------------------------------------------------------
# AgentDraftRepository
# ---------------------------------------------------------------------------


class AgentDraftRepository:
    """Owner-scoped draft reads, optimistic update, and sub-table replacement."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        self._before_cas = None
        self._after_publish_snapshot = None
        self._before_publish_identity_lock = None

    async def get(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        return await self._get_snapshot(agent_id, owner_user_id=owner_user_id)

    async def get_publish_snapshot(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Read the draft row, skills and grants with one SQL statement."""
        snapshot = await self._get_snapshot(agent_id, owner_user_id=owner_user_id)
        if snapshot is not None and self._after_publish_snapshot is not None:
            await self._after_publish_snapshot(snapshot)
        return snapshot

    async def _get_snapshot(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        stmt = (
            select(
                PublishedAgentRow,
                AgentDraftRow,
                AgentDraftSkillRow,
                AgentDraftConnectorGrantRow,
            )
            .join(AgentDraftRow, AgentDraftRow.agent_id == PublishedAgentRow.id)
            .outerjoin(
                AgentDraftSkillRow,
                AgentDraftSkillRow.agent_id == AgentDraftRow.agent_id,
            )
            .outerjoin(
                AgentDraftConnectorGrantRow,
                AgentDraftConnectorGrantRow.agent_id == AgentDraftRow.agent_id,
            )
            .where(
                PublishedAgentRow.id == agent_id,
                PublishedAgentRow.owner_user_id == owner_user_id,
            )
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).all()
            return _draft_from_joined_rows(rows)

    async def lock_revision_for_publish(
        self,
        session: AsyncSession,
        agent_id: str,
        *,
        owner_user_id: str,
        expected_revision: int,
    ) -> bool | None:
        """Lock the owner-scoped draft and compare its current revision.

        ``None`` means missing/not owned, ``False`` means stale, and ``True``
        keeps the row locked in the caller's transaction through pointer commit.
        """
        # Use the same global row-lock order as conversational authoring:
        # published_agents identity first, then agent_drafts. Loading the full
        # identity row also places it in this session's identity map, so the
        # later release pointer update reuses the already-locked object.
        identity_stmt = (
            select(PublishedAgentRow)
            .where(
                PublishedAgentRow.id == agent_id,
                PublishedAgentRow.owner_user_id == owner_user_id,
            )
            .with_for_update(of=PublishedAgentRow)
        )
        if self._before_publish_identity_lock is not None:
            await self._before_publish_identity_lock()
        identity = (await session.execute(identity_stmt)).scalar_one_or_none()
        if identity is None:
            return None
        draft_stmt = select(AgentDraftRow.revision).where(AgentDraftRow.agent_id == agent_id).with_for_update(of=AgentDraftRow)
        revision = (await session.execute(draft_stmt)).scalar_one_or_none()
        return None if revision is None else revision == expected_revision

    async def update_with_revision(
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
    ) -> dict[str, Any] | None:
        """Conditionally update fields via database-level CAS; ``None`` if stale.

        Uses a single conditional ``UPDATE ... WHERE agent_id = ? AND revision = ?``
        (plus an ownership guard) and checks the rowcount, so two transactions
        that both read the same revision cannot both succeed — only the winner's
        UPDATE matches a row (rereview Critical-2). Only fields explicitly passed
        (not ``None``) are written.
        """
        async with self._sf() as session:
            set_values: dict[str, Any] = {
                "revision": revision + 1,
                "updated_by": str(owner_user_id),
                "updated_at": _now(),
            }
            if agent_markdown is not None:
                set_values["agent_markdown"] = agent_markdown
            if soul_markdown is not None:
                set_values["soul_markdown"] = soul_markdown
            if model_name is not None:
                set_values["model_name"] = model_name
            if tool_groups is not None:
                set_values["tool_groups_json"] = list(tool_groups)
            if quota_overrides is not None:
                set_values["quota_overrides_json"] = dict(quota_overrides)
            owner_match = select(PublishedAgentRow.owner_user_id).where(PublishedAgentRow.id == AgentDraftRow.agent_id).scalar_subquery()
            stmt = (
                update(AgentDraftRow)
                .where(
                    AgentDraftRow.agent_id == agent_id,
                    AgentDraftRow.revision == revision,
                    owner_match == owner_user_id,
                )
                .values(**set_values)
            )
            if self._before_cas is not None:
                await self._before_cas()
            result = await session.execute(stmt)
            if result.rowcount != 1:
                await session.rollback()
                return None
            await session.commit()
            draft = await self._load(session, agent_id, owner_user_id=owner_user_id)
            if draft is None:
                return None
            skills = await self._load_skills(session, agent_id)
            grants = await self._load_grants(session, agent_id)
            return _draft_to_dict(draft, skills=skills, connector_grants=grants)

    async def update_bundle(
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
    ) -> dict[str, Any] | None:
        """Atomically update the draft main row and its sub-tables via DB-level CAS.

        The main row is updated with a single conditional ``UPDATE ... WHERE
        agent_id = ? AND revision = ?`` (plus an ownership guard through the
        ``published_agents`` join); the result rowcount tells us whether we won
        the revision. Only if we won (rowcount == 1) do we replace the sub-tables
        and commit — all in one transaction. This closes the lost-update window
        where two transactions both read revision=N, both pass the Python check,
        and both write revision=N+1 (rereview Critical-2). A stale or
        cross-owner call writes nothing and returns ``None``.
        """
        async with self._sf() as session:
            # Build a dynamic SET clause from the provided scalar fields.
            set_values: dict[str, Any] = {
                "revision": revision + 1,
                "updated_by": str(owner_user_id),
                "updated_at": _now(),
            }
            if agent_markdown is not None:
                set_values["agent_markdown"] = agent_markdown
            if soul_markdown is not None:
                set_values["soul_markdown"] = soul_markdown
            if model_name is not None:
                set_values["model_name"] = model_name
            if tool_groups is not None:
                set_values["tool_groups_json"] = list(tool_groups)
            if quota_overrides is not None:
                set_values["quota_overrides_json"] = dict(quota_overrides)
            if skills is not None:
                set_values["skill_selection_mode"] = "explicit"

            # Conditional UPDATE gated on both revision AND ownership (ownership
            # is enforced by joining published_agents inside the WHERE clause,
            # since agent_drafts has no denormalised owner column).
            owner_match = select(PublishedAgentRow.owner_user_id).where(PublishedAgentRow.id == AgentDraftRow.agent_id).scalar_subquery()
            stmt = (
                update(AgentDraftRow)
                .where(
                    AgentDraftRow.agent_id == agent_id,
                    AgentDraftRow.revision == revision,
                    owner_match == owner_user_id,
                )
                .values(**set_values)
            )
            if self._before_cas is not None:
                await self._before_cas()
            result = await session.execute(stmt)
            if result.rowcount != 1:
                # Either the draft doesn't exist, isn't owned by the caller, or
                # the revision was stale. Nothing was written.
                await session.rollback()
                return None

            # We won the revision; now replace the sub-tables in the same
            # transaction. These writes commit atomically with the main-row CAS.
            if skills is not None:
                await session.execute(delete(AgentDraftSkillRow).where(AgentDraftSkillRow.agent_id == agent_id))
                for entry in skills:
                    session.add(
                        AgentDraftSkillRow(
                            agent_id=agent_id,
                            skill_name=str(entry["skill_name"]),
                            source=str(entry.get("source", "public")),
                        )
                    )
            if connector_grants is not None:
                await session.execute(delete(AgentDraftConnectorGrantRow).where(AgentDraftConnectorGrantRow.agent_id == agent_id))
                for entry in connector_grants:
                    session.add(
                        AgentDraftConnectorGrantRow(
                            agent_id=agent_id,
                            connector_instance_id=str(entry["connector_instance_id"]),
                            capability=str(entry["capability"]),
                        )
                    )
            await session.commit()
            draft = await self._load(session, agent_id, owner_user_id=owner_user_id)
            if draft is None:
                return None
            skills_rows = await self._load_skills(session, agent_id)
            grants_rows = await self._load_grants(session, agent_id)
            return _draft_to_dict(draft, skills=skills_rows, connector_grants=grants_rows)

    async def replace_skills(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        skills: Sequence[Mapping[str, str]],
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            draft = await self._load(
                session,
                agent_id,
                owner_user_id=owner_user_id,
                for_update=True,
            )
            if draft is None:
                return None
            await session.execute(delete(AgentDraftSkillRow).where(AgentDraftSkillRow.agent_id == agent_id))
            draft.skill_selection_mode = "explicit"
            for entry in skills:
                session.add(
                    AgentDraftSkillRow(
                        agent_id=agent_id,
                        skill_name=str(entry["skill_name"]),
                        source=str(entry.get("source", "public")),
                    )
                )
            draft.revision += 1
            draft.updated_by = owner_user_id
            draft.updated_at = _now()
            await session.commit()
            skills_rows = await self._load_skills(session, agent_id)
            grants = await self._load_grants(session, agent_id)
            return _draft_to_dict(draft, skills=skills_rows, connector_grants=grants)

    async def replace_connector_grants(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        grants: Sequence[Mapping[str, str]],
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            draft = await self._load(
                session,
                agent_id,
                owner_user_id=owner_user_id,
                for_update=True,
            )
            if draft is None:
                return None
            await session.execute(delete(AgentDraftConnectorGrantRow).where(AgentDraftConnectorGrantRow.agent_id == agent_id))
            for entry in grants:
                session.add(
                    AgentDraftConnectorGrantRow(
                        agent_id=agent_id,
                        connector_instance_id=str(entry["connector_instance_id"]),
                        capability=str(entry["capability"]),
                    )
                )
            draft.revision += 1
            draft.updated_by = owner_user_id
            draft.updated_at = _now()
            await session.commit()
            skills = await self._load_skills(session, agent_id)
            grants_rows = await self._load_grants(session, agent_id)
            return _draft_to_dict(draft, skills=skills, connector_grants=grants_rows)

    async def _load(
        self,
        session: AsyncSession,
        agent_id: str,
        *,
        owner_user_id: str,
        for_update: bool = False,
    ) -> AgentDraftRow | None:
        """Load a draft only if it belongs to ``owner_user_id``.

        Ownership is enforced by joining against ``published_agents`` (the draft
        table has no denormalised owner column by design).
        """
        stmt = (
            select(AgentDraftRow)
            .join(PublishedAgentRow, PublishedAgentRow.id == AgentDraftRow.agent_id)
            .where(
                AgentDraftRow.agent_id == agent_id,
                PublishedAgentRow.owner_user_id == owner_user_id,
            )
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _load_skills(self, session: AsyncSession, agent_id: str) -> list[dict[str, str]]:
        return await _load_skill_dicts(session, agent_id)

    async def _load_grants(self, session: AsyncSession, agent_id: str) -> list[dict[str, str]]:
        return await _load_grant_dicts(session, agent_id)
