from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.user_extension.model import (
    UserImageProviderRow,
    UserImageSettingsRow,
    UserMcpServerRow,
    UserMcpServerStateRow,
)


def _now() -> datetime:
    return datetime.now(UTC)


class UserMcpServerRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _ensure_name_available(self, user_id: str, name: str, *, exclude_id: str | None = None) -> None:
        stmt = select(UserMcpServerRow).where(
            UserMcpServerRow.user_id == user_id,
            UserMcpServerRow.name == name,
            UserMcpServerRow.deleted_at.is_(None),
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        if any(row.id != exclude_id for row in rows):
            raise ValueError(f"MCP server name already exists: {name}")

    async def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        user_id = str(values["user_id"])
        name = str(values["name"])
        await self._ensure_name_available(user_id, name)
        row = UserMcpServerRow(
            id=str(values.get("id") or f"umcp_{uuid4().hex}"),
            user_id=user_id,
            name=name,
            enabled=bool(values.get("enabled", True)),
            type=str(values.get("type", "stdio")),
            command=values.get("command"),
            args=values.get("args") or [],
            url=values.get("url"),
            description=str(values.get("description") or ""),
            secrets_ref=values.get("secrets_ref"),
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _mcp_row_to_dict(row)

    async def get_by_name(self, user_id: str, name: str) -> dict[str, Any] | None:
        stmt = select(UserMcpServerRow).where(
            UserMcpServerRow.user_id == user_id,
            UserMcpServerRow.name == name,
            UserMcpServerRow.deleted_at.is_(None),
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _mcp_row_to_dict(row) if row else None

    async def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        stmt = select(UserMcpServerRow).where(UserMcpServerRow.user_id == user_id, UserMcpServerRow.deleted_at.is_(None)).order_by(UserMcpServerRow.created_at.desc())
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_mcp_row_to_dict(row) for row in rows]

    async def update(self, user_id: str, name: str, values: Mapping[str, Any]) -> dict[str, Any] | None:
        async with self._sf() as session:
            stmt = select(UserMcpServerRow).where(
                UserMcpServerRow.user_id == user_id,
                UserMcpServerRow.name == name,
                UserMcpServerRow.deleted_at.is_(None),
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            if "name" in values and values["name"] != row.name:
                await self._ensure_name_available(user_id, str(values["name"]), exclude_id=row.id)
                row.name = str(values["name"])
            for field in ("enabled", "type", "command", "args", "url", "description", "secrets_ref"):
                if field in values:
                    setattr(row, field, values[field])
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _mcp_row_to_dict(row)

    async def delete(self, user_id: str, name: str) -> bool:
        async with self._sf() as session:
            stmt = select(UserMcpServerRow).where(
                UserMcpServerRow.user_id == user_id,
                UserMcpServerRow.name == name,
                UserMcpServerRow.deleted_at.is_(None),
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return False
            row.deleted_at = _now()
            row.updated_at = _now()
            await session.commit()
            return True


class UserMcpServerStateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        stmt = select(UserMcpServerStateRow).where(UserMcpServerStateRow.user_id == user_id)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_state_row_to_dict(row) for row in rows]

    async def upsert(self, user_id: str, server_name: str, enabled: bool) -> dict[str, Any]:
        async with self._sf() as session:
            row = await session.get(UserMcpServerStateRow, {"user_id": user_id, "server_name": server_name})
            if row is None:
                row = UserMcpServerStateRow(user_id=user_id, server_name=server_name, enabled=enabled)
                session.add(row)
            else:
                row.enabled = enabled
                row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _state_row_to_dict(row)


class UserImageSettingsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(UserImageSettingsRow, user_id)
            return _image_settings_row_to_dict(row) if row else None

    async def upsert(self, user_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        async with self._sf() as session:
            row = await session.get(UserImageSettingsRow, user_id)
            if row is None:
                row = UserImageSettingsRow(
                    user_id=user_id,
                    enabled=bool(values.get("enabled", False)),
                    default_provider=values.get("default_provider"),
                    output_subdir=str(values.get("output_subdir") or "generated-images"),
                )
                session.add(row)
            else:
                if "enabled" in values:
                    row.enabled = bool(values["enabled"])
                if "default_provider" in values:
                    row.default_provider = values["default_provider"]
                if "output_subdir" in values:
                    row.output_subdir = str(values["output_subdir"] or "generated-images")
                row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _image_settings_row_to_dict(row)


class UserImageProviderRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        stmt = select(UserImageProviderRow).where(UserImageProviderRow.user_id == user_id, UserImageProviderRow.deleted_at.is_(None)).order_by(UserImageProviderRow.provider)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_image_provider_row_to_dict(row) for row in rows]

    async def upsert_provider(self, user_id: str, provider: str, values: Mapping[str, Any]) -> dict[str, Any]:
        async with self._sf() as session:
            stmt = select(UserImageProviderRow).where(
                UserImageProviderRow.user_id == user_id,
                UserImageProviderRow.provider == provider,
                UserImageProviderRow.deleted_at.is_(None),
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                row = UserImageProviderRow(
                    id=str(values.get("id") or f"uimg_{uuid4().hex}"),
                    user_id=user_id,
                    provider=provider,
                    enabled=bool(values.get("enabled", False)),
                    api_key_ref=values.get("api_key_ref"),
                    api_key_last_four=values.get("api_key_last_four"),
                    base_url=values.get("base_url"),
                    model=values.get("model"),
                    timeout_seconds=float(values.get("timeout_seconds", 120.0)),
                    trust_env=bool(values.get("trust_env", False)),
                    params=values.get("params") or {},
                )
                session.add(row)
            else:
                for field in (
                    "enabled",
                    "api_key_ref",
                    "api_key_last_four",
                    "base_url",
                    "model",
                    "timeout_seconds",
                    "trust_env",
                    "params",
                ):
                    if field in values:
                        setattr(row, field, values[field])
                row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _image_provider_row_to_dict(row)


def _mcp_row_to_dict(row: UserMcpServerRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "enabled": row.enabled,
        "type": row.type,
        "command": row.command,
        "args": row.args or [],
        "url": row.url,
        "description": row.description,
        "secrets_ref": row.secrets_ref,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _state_row_to_dict(row: UserMcpServerStateRow) -> dict[str, Any]:
    return {
        "user_id": row.user_id,
        "server_name": row.server_name,
        "enabled": row.enabled,
        "updated_at": row.updated_at,
    }


def _image_settings_row_to_dict(row: UserImageSettingsRow) -> dict[str, Any]:
    return {
        "user_id": row.user_id,
        "enabled": row.enabled,
        "default_provider": row.default_provider,
        "output_subdir": row.output_subdir,
        "updated_at": row.updated_at,
    }


def _image_provider_row_to_dict(row: UserImageProviderRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "provider": row.provider,
        "enabled": row.enabled,
        "api_key_ref": row.api_key_ref,
        "api_key_last_four": row.api_key_last_four,
        "base_url": row.base_url,
        "model": row.model,
        "timeout_seconds": row.timeout_seconds,
        "trust_env": row.trust_env,
        "params": row.params or {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
