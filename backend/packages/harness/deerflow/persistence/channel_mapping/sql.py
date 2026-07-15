"""SQL persistence for binding-scoped conversations and event deduplication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.agent_channel.model import AgentChannelRow
from deerflow.persistence.channel_mapping.model import ChannelConversationMappingRow, ChannelEventDedupRow
from deerflow.persistence.published_agent.model import PublishedAgentRow

_GROUP_ACTOR_SCOPE = "group"
_MAPPING_CONFLICT_COLUMNS = ["binding_id", "chat_id", "actor_scope", "topic_id"]


class _SystemChannelMappingScope:
    pass


SYSTEM_CHANNEL_MAPPING_SCOPE = _SystemChannelMappingScope()


def _now() -> datetime:
    return datetime.now(UTC)


def _require(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


class MappingScopeConflictError(RuntimeError):
    """Raised if a stable binding is unexpectedly presented with another Agent."""


class ChannelMappingRepository:
    """Concurrency-safe persistent mappings for Published-Agent channels."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Bind persistent mapping operations to an async session factory."""
        self._sf = session_factory

    @staticmethod
    def _scope(
        *,
        chat_type: Literal["p2p", "group"],
        feishu_user_id: str,
        topic_id: str | None,
    ) -> tuple[str, str]:
        if chat_type == "p2p":
            return f"user:{_require(feishu_user_id, 'feishu_user_id')}", ""
        if chat_type == "group":
            return _GROUP_ACTOR_SCOPE, (topic_id or "").strip()
        raise ValueError("chat_type must be 'p2p' or 'group'")

    async def get_or_create_thread(
        self,
        *,
        binding_id: str,
        agent_id: str,
        chat_id: str,
        feishu_user_id: str,
        chat_type: Literal["p2p", "group"],
        topic_id: str | None = None,
        system_scope: object,
    ) -> str:
        """Atomically resolve a trusted inbound binding to one stable thread.

        Only the unforgeable system scope may use this cross-owner ingress path;
        the repository still verifies the binding's stable Agent assignment.
        """
        if system_scope is not SYSTEM_CHANNEL_MAPPING_SCOPE:
            raise PermissionError("system channel mapping scope required")
        binding_id = _require(binding_id, "binding_id")
        agent_id = _require(agent_id, "agent_id")
        chat_id = _require(chat_id, "chat_id")
        actor_scope, normalized_topic = self._scope(
            chat_type=chat_type,
            feishu_user_id=feishu_user_id,
            topic_id=topic_id,
        )
        candidate_thread_id = str(uuid4())
        values = {
            "id": f"ccm_{uuid4().hex}",
            "binding_id": binding_id,
            "agent_id": agent_id,
            "chat_id": chat_id,
            "actor_scope": actor_scope,
            "topic_id": normalized_topic,
            "thread_id": candidate_thread_id,
            "created_at": _now(),
            "updated_at": _now(),
        }

        async with self._sf() as session:
            bound_agent_id = (await session.execute(select(AgentChannelRow.agent_id).join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id).where(AgentChannelRow.id == binding_id))).scalar_one_or_none()
            if bound_agent_id != agent_id:
                raise MappingScopeConflictError("binding is not assigned to the requested Agent")
            dialect = session.bind.dialect.name if session.bind is not None else ""
            inserted_thread_id: str | None = None
            if dialect == "sqlite":
                statement = sqlite_insert(ChannelConversationMappingRow).values(**values)
                statement = statement.on_conflict_do_nothing(index_elements=_MAPPING_CONFLICT_COLUMNS)
                inserted_thread_id = (await session.execute(statement.returning(ChannelConversationMappingRow.thread_id))).scalar_one_or_none()
                await session.commit()
            elif dialect == "postgresql":
                statement = postgresql_insert(ChannelConversationMappingRow).values(**values)
                statement = statement.on_conflict_do_nothing(index_elements=_MAPPING_CONFLICT_COLUMNS)
                inserted_thread_id = (await session.execute(statement.returning(ChannelConversationMappingRow.thread_id))).scalar_one_or_none()
                await session.commit()
            else:
                session.add(ChannelConversationMappingRow(**values))
                try:
                    await session.commit()
                    inserted_thread_id = candidate_thread_id
                except IntegrityError:
                    await session.rollback()

            if inserted_thread_id is not None:
                return str(inserted_thread_id)

            row = (
                await session.execute(
                    select(ChannelConversationMappingRow).where(
                        ChannelConversationMappingRow.binding_id == binding_id,
                        ChannelConversationMappingRow.chat_id == chat_id,
                        ChannelConversationMappingRow.actor_scope == actor_scope,
                        ChannelConversationMappingRow.topic_id == normalized_topic,
                    )
                )
            ).scalar_one()
            if row.agent_id != agent_id:
                raise MappingScopeConflictError("binding conversation is already assigned to another Agent")
            return row.thread_id

    async def list_mappings(
        self,
        *,
        binding_id: str,
        owner_user_id: str,
    ) -> list[ChannelConversationMappingRow]:
        """List one owner's mappings without exposing other tenant thread IDs."""
        async with self._sf() as session:
            statement = (
                select(ChannelConversationMappingRow)
                .join(
                    AgentChannelRow,
                    (AgentChannelRow.id == ChannelConversationMappingRow.binding_id) & (AgentChannelRow.agent_id == ChannelConversationMappingRow.agent_id),
                )
                .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
                .where(
                    ChannelConversationMappingRow.binding_id == binding_id,
                    PublishedAgentRow.owner_user_id == owner_user_id,
                )
                .order_by(ChannelConversationMappingRow.created_at)
            )
            return list((await session.execute(statement)).scalars().all())


class ChannelEventRepository:
    """Atomically claim inbound events before they reach quota or execution."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        retention_hours: int = 72,
    ) -> None:
        """Configure binding-scoped event claims and their retention window."""
        if retention_hours <= 0:
            raise ValueError("retention_hours must be positive")
        self._sf = session_factory
        self._retention = timedelta(hours=retention_hours)

    async def claim(
        self,
        binding_id: str,
        event_id: str,
        *,
        system_scope: object,
        now: datetime | None = None,
    ) -> bool:
        """Claim an event only for a trusted, persisted Feishu binding."""
        if system_scope is not SYSTEM_CHANNEL_MAPPING_SCOPE:
            raise PermissionError("system channel mapping scope required")
        binding_id = _require(binding_id, "binding_id")
        event_id = _require(event_id, "event_id")
        claimed_at = now or _now()
        values = {"binding_id": binding_id, "event_id": event_id, "created_at": claimed_at}

        async with self._sf() as session:
            persisted_binding = (
                await session.execute(
                    select(AgentChannelRow.id)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
                    .where(
                        AgentChannelRow.id == binding_id,
                        AgentChannelRow.channel_type == "feishu",
                    )
                )
            ).scalar_one_or_none()
            if persisted_binding is None:
                raise MappingScopeConflictError("event claim requires a valid Feishu binding")
            await session.execute(delete(ChannelEventDedupRow).where(ChannelEventDedupRow.created_at < claimed_at - self._retention))
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "sqlite":
                statement = sqlite_insert(ChannelEventDedupRow).values(**values)
                statement = statement.on_conflict_do_nothing(index_elements=["binding_id", "event_id"])
                result = await session.execute(statement.returning(ChannelEventDedupRow.event_id))
                claimed = result.scalar_one_or_none() is not None
                await session.commit()
                return claimed
            if dialect == "postgresql":
                statement = postgresql_insert(ChannelEventDedupRow).values(**values)
                statement = statement.on_conflict_do_nothing(index_elements=["binding_id", "event_id"])
                result = await session.execute(statement.returning(ChannelEventDedupRow.event_id))
                claimed = result.scalar_one_or_none() is not None
                await session.commit()
                return claimed

            session.add(ChannelEventDedupRow(**values))
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False


__all__ = [
    "ChannelEventRepository",
    "ChannelMappingRepository",
    "MappingScopeConflictError",
    "SYSTEM_CHANNEL_MAPPING_SCOPE",
]
