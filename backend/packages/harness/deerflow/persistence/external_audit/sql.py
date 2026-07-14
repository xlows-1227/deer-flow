from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.external_audit.model import ExternalAuditRow


class ExternalAuditRepository:
    """Append and query metadata-only external API audit events."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def append(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Append one immutable audit event."""
        row = ExternalAuditRow(id=str(values.get("id") or f"audit_{uuid4().hex}"), **{k: v for k, v in values.items() if k != "id"})
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def list(
        self,
        *,
        user_id: str | None = None,
        api_key_id: str | None = None,
        owner_user_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List recent events within a required principal and owner scope."""
        if user_id is None and api_key_id is None and owner_user_id is None and agent_id is None:
            raise ValueError("at least one audit principal scope is required")
        if agent_id is not None and owner_user_id is None:
            raise ValueError("owner_user_id is required when querying Agent audit events")
        stmt = select(ExternalAuditRow).order_by(ExternalAuditRow.created_at.desc()).limit(limit)
        if user_id is not None:
            stmt = stmt.where(ExternalAuditRow.user_id == user_id)
        if api_key_id is not None:
            stmt = stmt.where(ExternalAuditRow.api_key_id == api_key_id)
        if owner_user_id is not None:
            stmt = stmt.where(ExternalAuditRow.owner_user_id == owner_user_id)
        if agent_id is not None:
            stmt = stmt.where(ExternalAuditRow.agent_id == agent_id)
        async with self._sf() as session:
            return [row.to_dict() for row in (await session.execute(stmt)).scalars().all()]
