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

from sqlalchemy import delete, select
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


# ---------------------------------------------------------------------------
# PublishedAgentRepository
# ---------------------------------------------------------------------------


class PublishedAgentRepository:
    """Owner-scoped CRUD for the stable ``published_agents`` identity table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create_agent(
        self,
        *,
        owner_user_id: str,
        slug: str,
        display_name: str,
        description: str | None = None,
        avatar_ref: str | None = None,
        agent_id: str | None = None,
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
        draft = AgentDraftRow(agent_id=row.id, updated_by=str(owner_user_id))
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

    async def get(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            draft = await self._load(session, agent_id, owner_user_id=owner_user_id)
            if draft is None:
                return None
            skills = await self._load_skills(session, agent_id)
            grants = await self._load_grants(session, agent_id)
            return _draft_to_dict(draft, skills=skills, connector_grants=grants)

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
        """Conditionally update fields; returns ``None`` when revision is stale.

        Only fields explicitly passed (not ``None``) are written, so callers can
        do partial updates. ``model_name`` may be cleared by passing an empty
        string is intentionally not supported here — pass ``None`` to leave it
        untouched. Bumping ``revision`` happens atomically in the same UPDATE.
        """
        async with self._sf() as session:
            draft = await self._load(session, agent_id, owner_user_id=owner_user_id)
            if draft is None or draft.revision != revision:
                return None
            if agent_markdown is not None:
                draft.agent_markdown = agent_markdown
            if soul_markdown is not None:
                draft.soul_markdown = soul_markdown
            if model_name is not None:
                draft.model_name = model_name
            if tool_groups is not None:
                draft.tool_groups_json = list(tool_groups)
            if quota_overrides is not None:
                draft.quota_overrides_json = dict(quota_overrides)
            draft.revision = revision + 1
            draft.updated_by = str(owner_user_id)
            draft.updated_at = _now()
            await session.commit()
            await session.refresh(draft)
            skills = await self._load_skills(session, agent_id)
            grants = await self._load_grants(session, agent_id)
            return _draft_to_dict(draft, skills=skills, connector_grants=grants)

    async def replace_skills(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        skills: Sequence[Mapping[str, str]],
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            draft = await self._load(session, agent_id, owner_user_id=owner_user_id)
            if draft is None:
                return None
            await session.execute(delete(AgentDraftSkillRow).where(AgentDraftSkillRow.agent_id == agent_id))
            for entry in skills:
                session.add(
                    AgentDraftSkillRow(
                        agent_id=agent_id,
                        skill_name=str(entry["skill_name"]),
                        source=str(entry.get("source", "public")),
                    )
                )
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
            draft = await self._load(session, agent_id, owner_user_id=owner_user_id)
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
            await session.commit()
            skills = await self._load_skills(session, agent_id)
            grants_rows = await self._load_grants(session, agent_id)
            return _draft_to_dict(draft, skills=skills, connector_grants=grants_rows)

    async def _load(self, session: AsyncSession, agent_id: str, *, owner_user_id: str) -> AgentDraftRow | None:
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
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _load_skills(self, session: AsyncSession, agent_id: str) -> list[dict[str, str]]:
        rows = (await session.execute(select(AgentDraftSkillRow).where(AgentDraftSkillRow.agent_id == agent_id).order_by(AgentDraftSkillRow.skill_name))).scalars().all()
        return [_skill_to_dict(row) for row in rows]

    async def _load_grants(self, session: AsyncSession, agent_id: str) -> list[dict[str, str]]:
        rows = (
            (await session.execute(select(AgentDraftConnectorGrantRow).where(AgentDraftConnectorGrantRow.agent_id == agent_id).order_by(AgentDraftConnectorGrantRow.connector_instance_id, AgentDraftConnectorGrantRow.capability)))
            .scalars()
            .all()
        )
        return [_grant_to_dict(row) for row in rows]
