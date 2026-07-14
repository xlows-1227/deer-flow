"""Owner-scoped repository for Published-Agent channel bindings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.agent_channel.model import AgentChannelRow
from deerflow.persistence.published_agent.model import PublishedAgentRow


def _now() -> datetime:
    return datetime.now(UTC)


class ActiveAgentChannelConflictError(RuntimeError):
    """Raised when an Agent already has an active binding of this type."""


class _SystemChannelSupervisorScope:
    pass


SYSTEM_CHANNEL_SUPERVISOR_SCOPE = _SystemChannelSupervisorScope()


def _to_dict(row: AgentChannelRow, *, owner_user_id: str | None = None) -> dict[str, Any]:
    value = row.to_dict()
    if owner_user_id is not None:
        value["owner_user_id"] = owner_user_id
    return value


class AgentChannelRepository:
    """CRUD for channel bindings with explicit owner isolation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _owned_query(agent_id: str, binding_id: str, owner_user_id: str):
        return (
            select(AgentChannelRow)
            .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
            .where(
                AgentChannelRow.id == binding_id,
                AgentChannelRow.agent_id == agent_id,
                PublishedAgentRow.owner_user_id == owner_user_id,
            )
        )

    async def owns_agent(self, agent_id: str, *, owner_user_id: str) -> bool:
        """Return whether ``owner_user_id`` owns the stable Agent identity."""
        async with self._sf() as session:
            value = (
                await session.execute(
                    select(PublishedAgentRow.id).where(
                        PublishedAgentRow.id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            return value is not None

    async def create(
        self,
        *,
        agent_id: str,
        owner_user_id: str,
        app_id: str,
        secret_ref: str,
        channel_type: str = "feishu",
        connection_mode: str = "websocket",
    ) -> dict[str, Any] | None:
        """Create an inactive binding when the caller owns the Agent."""
        async with self._sf() as session:
            owner = (
                await session.execute(
                    select(PublishedAgentRow.id).where(
                        PublishedAgentRow.id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if owner is None:
                return None
            row = AgentChannelRow(
                id=f"ach_{uuid4().hex}",
                agent_id=agent_id,
                channel_type=channel_type,
                app_id=app_id,
                secret_ref=secret_ref,
                connection_mode=connection_mode,
                status="inactive",
                health="unknown",
            )
            session.add(row)
            await session.commit()
            return _to_dict(row)

    async def get(self, agent_id: str, binding_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id))).scalar_one_or_none()
            return _to_dict(row) if row is not None else None

    async def list_by_agent(self, agent_id: str, *, owner_user_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentChannelRow)
                        .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
                        .where(
                            AgentChannelRow.agent_id == agent_id,
                            PublishedAgentRow.owner_user_id == owner_user_id,
                        )
                        .order_by(AgentChannelRow.created_at, AgentChannelRow.id)
                    )
                )
                .scalars()
                .all()
            )
            return [_to_dict(row) for row in rows]

    async def list_active(self, *, supervisor_scope: object) -> list[dict[str, Any]]:
        """Return all desired-active bindings to the trusted Supervisor only."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(AgentChannelRow, PublishedAgentRow.owner_user_id)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
                    .where(AgentChannelRow.status == "active")
                    .order_by(AgentChannelRow.created_at, AgentChannelRow.id)
                )
            ).all()
            return [_to_dict(row, owner_user_id=str(owner_user_id)) for row, owner_user_id in rows]

    async def get_for_supervisor(self, binding_id: str, *, supervisor_scope: object) -> dict[str, Any] | None:
        """Resolve one binding across owners for the trusted Supervisor only."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            result = (await session.execute(select(AgentChannelRow, PublishedAgentRow.owner_user_id).join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id).where(AgentChannelRow.id == binding_id))).one_or_none()
            if result is None:
                return None
            row, owner_user_id = result
            return _to_dict(row, owner_user_id=str(owner_user_id))

    async def activate(self, agent_id: str, binding_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return None
            row.status = "active"
            row.health = "unknown"
            row.health_detail = None
            row.last_started_at = _now()
            row.updated_at = _now()
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ActiveAgentChannelConflictError("Agent already has an active channel binding") from exc
            return _to_dict(row)

    async def deactivate(self, agent_id: str, binding_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return None
            row.status = "inactive"
            row.health = "unknown"
            row.health_detail = None
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def update_credentials(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        app_id: str,
        secret_ref: str,
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return None
            row.app_id = app_id
            row.secret_ref = secret_ref
            row.health = "unknown"
            row.health_detail = None
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def update_health(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        health: str,
        detail: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return None
            row.health = health
            row.health_detail = detail[:512] if detail else None
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def delete(self, agent_id: str, binding_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return None
            value = _to_dict(row)
            await session.delete(row)
            await session.commit()
            return value


__all__ = [
    "ActiveAgentChannelConflictError",
    "AgentChannelRepository",
    "SYSTEM_CHANNEL_SUPERVISOR_SCOPE",
]
