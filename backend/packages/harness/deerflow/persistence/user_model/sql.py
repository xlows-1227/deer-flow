from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.user_model.model import UserModelRow


def _now() -> datetime:
    return datetime.now(UTC)


def _row_to_dict(row: UserModelRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "display_name": row.display_name,
        "provider": row.provider,
        "model": row.model,
        "base_url": row.base_url,
        "api_key_ref": row.api_key_ref,
        "api_key_last_four": row.api_key_last_four,
        "enabled": row.enabled,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class UserModelRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _ensure_name_available(self, user_id: str, name: str, *, exclude_id: str | None = None) -> None:
        stmt = select(UserModelRow).where(
            UserModelRow.user_id == user_id,
            UserModelRow.name == name,
            UserModelRow.deleted_at.is_(None),
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        if any(row.id != exclude_id for row in rows):
            raise ValueError(f"Model name already exists: {name}")

    async def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        user_id = str(values["user_id"])
        name = str(values["name"])
        await self._ensure_name_available(user_id, name)
        row = UserModelRow(
            id=str(values.get("id") or f"umodel_{uuid4().hex}"),
            user_id=user_id,
            name=name,
            display_name=values.get("display_name"),
            provider=str(values["provider"]),
            model=str(values["model"]),
            base_url=values.get("base_url"),
            api_key_ref=values.get("api_key_ref"),
            api_key_last_four=values.get("api_key_last_four"),
            enabled=bool(values.get("enabled", True)),
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def get(self, model_id: str, *, user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(UserModelRow, model_id)
            if row is None or row.deleted_at is not None or row.user_id != user_id:
                return None
            return _row_to_dict(row)

    async def list_for_user(self, user_id: str, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        stmt = select(UserModelRow).where(UserModelRow.user_id == user_id, UserModelRow.deleted_at.is_(None))
        if not include_disabled:
            stmt = stmt.where(UserModelRow.enabled.is_(True))
        stmt = stmt.order_by(UserModelRow.created_at.desc())
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_dict(row) for row in rows]

    async def update(self, model_id: str, values: Mapping[str, Any], *, user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(UserModelRow, model_id)
            if row is None or row.deleted_at is not None or row.user_id != user_id:
                return None
            if "name" in values and values["name"] != row.name:
                await self._ensure_name_available(user_id, str(values["name"]), exclude_id=model_id)
                row.name = str(values["name"])
            for field in (
                "display_name",
                "provider",
                "model",
                "base_url",
                "api_key_ref",
                "api_key_last_four",
                "enabled",
            ):
                if field in values:
                    setattr(row, field, values[field])
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def delete(self, model_id: str, *, user_id: str) -> bool:
        async with self._sf() as session:
            row = await session.get(UserModelRow, model_id)
            if row is None or row.deleted_at is not None or row.user_id != user_id:
                return False
            row.deleted_at = _now()
            row.updated_at = _now()
            await session.commit()
            return True
