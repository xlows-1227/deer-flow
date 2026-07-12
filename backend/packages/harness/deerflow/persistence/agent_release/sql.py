"""Repository for agent_releases — immutable writes, owner-scoped reads.

By design this repository exposes only ``create`` and read methods
(``get``, ``list_by_agent``, ``get_by_release_no``, ``next_release_no``).
There are no update/delete mutators on release rows, so immutability is
enforced at the API surface (the test suite asserts this with ``dir``).
Rollbacks repoint ``published_agents.current_release_id`` rather than touch
release history.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.agent_release.model import (
    AgentReleaseConnectorGrantRow,
    AgentReleaseRow,
    AgentReleaseSkillRow,
)
from deerflow.persistence.published_agent.model import PublishedAgentRow


def _now() -> datetime:
    return datetime.now(UTC)


def _release_to_dict(
    row: AgentReleaseRow,
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


class AgentReleaseRepository:
    """Immutable write-once release snapshots with owner-scoped reads."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def next_release_no(self, agent_id: str) -> int:
        """Return the next ``release_no`` for an agent (1 if none yet)."""
        async with self._sf() as session:
            current = await session.execute(
                select(func.max(AgentReleaseRow.release_no)).where(AgentReleaseRow.agent_id == agent_id)
            )
            value = current.scalar_one()
            return int(value) + 1 if value else 1

    async def create(
        self,
        values: Mapping[str, Any],
        *,
        skills: Sequence[Mapping[str, str]] | None = None,
        connector_grants: Sequence[Mapping[str, str]] | None = None,
    ) -> dict[str, Any]:
        row = AgentReleaseRow(
            id=str(values.get("id") or f"rel_{uuid4().hex}"),
            agent_id=str(values["agent_id"]),
            release_no=int(values["release_no"]),
            agent_markdown=str(values.get("agent_markdown") or ""),
            soul_markdown=str(values.get("soul_markdown") or ""),
            model_name=values.get("model_name"),
            tool_groups_json=list(values.get("tool_groups") or []),
            quota_overrides_json=dict(values.get("quota_overrides") or {}),
            manifest_checksum=str(values["manifest_checksum"]),
            created_by=str(values["created_by"]),
        )
        async with self._sf() as session:
            session.add(row)
            for entry in skills or []:
                session.add(
                    AgentReleaseSkillRow(
                        release_id=row.id,
                        skill_revision_id=str(entry["skill_revision_id"]),
                    )
                )
            for entry in connector_grants or []:
                session.add(
                    AgentReleaseConnectorGrantRow(
                        release_id=row.id,
                        connector_instance_id=str(entry["connector_instance_id"]),
                        capability=str(entry["capability"]),
                    )
                )
            await session.commit()
            await session.refresh(row)
            skill_rows = await self._load_skills(session, row.id)
            grant_rows = await self._load_grants(session, row.id)
            return _release_to_dict(row, skills=skill_rows, connector_grants=grant_rows)

    async def get(self, release_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await self._load_for_owner(session, release_id, owner_user_id)
            if row is None:
                return None
            skills = await self._load_skills(session, release_id)
            grants = await self._load_grants(session, release_id)
            return _release_to_dict(row, skills=skills, connector_grants=grants)

    async def list_by_agent(self, agent_id: str, *, owner_user_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            # Confirm ownership via the published_agents table before listing.
            agent = await session.get(PublishedAgentRow, agent_id)
            if agent is None or agent.owner_user_id != owner_user_id:
                return []
            rows = (
                await session.execute(
                    select(AgentReleaseRow)
                    .where(AgentReleaseRow.agent_id == agent_id)
                    .order_by(AgentReleaseRow.release_no.desc())
                )
            ).scalars().all()
            results = []
            for row in rows:
                skills = await self._load_skills(session, row.id)
                grants = await self._load_grants(session, row.id)
                results.append(_release_to_dict(row, skills=skills, connector_grants=grants))
            return results

    async def get_by_release_no(
        self,
        agent_id: str,
        *,
        release_no: int,
        owner_user_id: str,
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            agent = await session.get(PublishedAgentRow, agent_id)
            if agent is None or agent.owner_user_id != owner_user_id:
                return None
            row = (
                await session.execute(
                    select(AgentReleaseRow).where(
                        AgentReleaseRow.agent_id == agent_id,
                        AgentReleaseRow.release_no == release_no,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            skills = await self._load_skills(session, row.id)
            grants = await self._load_grants(session, row.id)
            return _release_to_dict(row, skills=skills, connector_grants=grants)

    async def _load_for_owner(
        self,
        session: AsyncSession,
        release_id: str,
        owner_user_id: str,
    ) -> AgentReleaseRow | None:
        stmt = (
            select(AgentReleaseRow)
            .join(PublishedAgentRow, PublishedAgentRow.id == AgentReleaseRow.agent_id)
            .where(
                AgentReleaseRow.id == release_id,
                PublishedAgentRow.owner_user_id == owner_user_id,
            )
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _load_skills(self, session: AsyncSession, release_id: str) -> list[dict[str, str]]:
        rows = (
            await session.execute(
                select(AgentReleaseSkillRow).where(AgentReleaseSkillRow.release_id == release_id)
            )
        ).scalars().all()
        return [{"skill_revision_id": row.skill_revision_id} for row in rows]

    async def _load_grants(self, session: AsyncSession, release_id: str) -> list[dict[str, str]]:
        rows = (
            await session.execute(
                select(AgentReleaseConnectorGrantRow)
                .where(AgentReleaseConnectorGrantRow.release_id == release_id)
                .order_by(
                    AgentReleaseConnectorGrantRow.connector_instance_id,
                    AgentReleaseConnectorGrantRow.capability,
                )
            )
        ).scalars().all()
        return [{"connector_instance_id": row.connector_instance_id, "capability": row.capability} for row in rows]
