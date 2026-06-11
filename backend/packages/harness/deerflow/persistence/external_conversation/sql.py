from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.external_conversation.model import ExternalConversationRow


class ExternalConversationExistsError(ValueError):
    def __init__(self, conversation_id: str) -> None:
        super().__init__("External conversation mapping already exists")
        self.conversation_id = conversation_id


class ExternalConversationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        user_id = str(values["user_id"])
        source = str(values.get("source") or "default")
        external_id = values.get("external_conversation_id")
        async with self._sf() as session:
            if external_id is not None:
                existing = (
                    await session.execute(
                        select(ExternalConversationRow).where(
                            ExternalConversationRow.user_id == user_id,
                            ExternalConversationRow.source == source,
                            ExternalConversationRow.external_conversation_id == str(external_id),
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    raise ExternalConversationExistsError(existing.conversation_id)
            row = ExternalConversationRow(
                conversation_id=str(values["conversation_id"]),
                user_id=user_id,
                source=source,
                external_conversation_id=str(external_id) if external_id is not None else None,
                thread_id=str(values["thread_id"]),
                agent_id=str(values["agent_id"]),
                default_skill_name=values.get("default_skill_name"),
                title=values.get("title"),
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if external_id is None:
                    raise
                existing = (
                    await session.execute(
                        select(ExternalConversationRow).where(
                            ExternalConversationRow.user_id == user_id,
                            ExternalConversationRow.source == source,
                            ExternalConversationRow.external_conversation_id == str(external_id),
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    raise
                raise ExternalConversationExistsError(existing.conversation_id) from None
            await session.refresh(row)
            return row.to_dict()

    async def get(self, conversation_id: str, *, user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(ExternalConversationRow).where(
                        ExternalConversationRow.conversation_id == conversation_id,
                        ExternalConversationRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            return row.to_dict() if row else None

    async def get_by_external_id(self, *, user_id: str, source: str, external_conversation_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(ExternalConversationRow).where(
                        ExternalConversationRow.user_id == user_id,
                        ExternalConversationRow.source == source,
                        ExternalConversationRow.external_conversation_id == external_conversation_id,
                    )
                )
            ).scalar_one_or_none()
            return row.to_dict() if row else None
