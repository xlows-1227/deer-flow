from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.api_key.model import APIKeyRow


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _to_dict(row: APIKeyRow) -> dict[str, Any]:
    data = row.to_dict()
    data["scopes"] = list(data.pop("scopes_json") or [])
    data["allowed_skills"] = list(data.pop("allowed_skills_json") or [])
    return data


class APIKeyRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def rotate(self, values: Mapping[str, Any]) -> dict[str, Any]:
        now = _now()
        async with self._sf() as session:
            active = (await session.execute(select(APIKeyRow).where(APIKeyRow.user_id == str(values["user_id"]), APIKeyRow.status == "active"))).scalars().all()
            for old in active:
                old.status = "revoked"
                old.revoked_at = now
                old.revoked_reason = "rotated"
            row = APIKeyRow(
                id=str(values["id"]),
                user_id=str(values["user_id"]),
                name=str(values.get("name") or "Default external API key"),
                secret_hash=str(values["secret_hash"]),
                key_prefix=str(values["key_prefix"]),
                last_four=str(values["last_four"]),
                scopes_json=list(values.get("scopes") or []),
                allowed_skills_json=list(values.get("allowed_skills") or []),
                expires_at=values.get("expires_at"),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_dict(row)

    async def get_active_by_id(self, key_id: str) -> dict[str, Any] | None:
        now = _now()
        stmt = select(APIKeyRow).where(
            APIKeyRow.id == key_id,
            APIKeyRow.status == "active",
            APIKeyRow.revoked_at.is_(None),
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None or (row.expires_at is not None and _as_utc(row.expires_at) <= now):
                return None
            return _to_dict(row)

    async def get_current_for_user(self, user_id: str) -> dict[str, Any] | None:
        stmt = select(APIKeyRow).where(APIKeyRow.user_id == user_id, APIKeyRow.status == "active")
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _to_dict(row) if row else None

    async def update_policy(self, user_id: str, allowed_skills: list[str]) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(select(APIKeyRow).where(APIKeyRow.user_id == user_id, APIKeyRow.status == "active"))).scalar_one_or_none()
            if row is None:
                return None
            row.allowed_skills_json = list(allowed_skills)
            await session.commit()
            await session.refresh(row)
            return _to_dict(row)

    async def revoke(self, user_id: str, *, reason: str = "revoked") -> bool:
        async with self._sf() as session:
            rows = (await session.execute(select(APIKeyRow).where(APIKeyRow.user_id == user_id, APIKeyRow.status == "active"))).scalars().all()
            for row in rows:
                row.status = "revoked"
                row.revoked_at = _now()
                row.revoked_reason = reason
            await session.commit()
            return bool(rows)

    async def touch_last_used(self, key_id: str) -> None:
        async with self._sf() as session:
            row = await session.get(APIKeyRow, key_id)
            if row:
                row.last_used_at = _now()
                await session.commit()
