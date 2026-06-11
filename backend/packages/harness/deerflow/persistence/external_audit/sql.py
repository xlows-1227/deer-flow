from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.external_audit.model import ExternalAuditRow


class ExternalAuditRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def append(self, values: Mapping[str, Any]) -> dict[str, Any]:
        row = ExternalAuditRow(id=str(values.get("id") or f"audit_{uuid4().hex}"), **{k: v for k, v in values.items() if k != "id"})
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def list(self, *, user_id: str | None = None, api_key_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        stmt = select(ExternalAuditRow).order_by(ExternalAuditRow.created_at.desc()).limit(limit)
        if user_id is not None:
            stmt = stmt.where(ExternalAuditRow.user_id == user_id)
        if api_key_id is not None:
            stmt = stmt.where(ExternalAuditRow.api_key_id == api_key_id)
        async with self._sf() as session:
            return [row.to_dict() for row in (await session.execute(stmt)).scalars().all()]
