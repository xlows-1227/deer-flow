from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.connector.model import ConnectorAuditLogRow, ConnectorGrantRow, ConnectorInstanceRow, ConnectorMetadataCacheRow


def _now() -> datetime:
    return datetime.now(UTC)


def _row_to_instance(row: ConnectorInstanceRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "owner_id": row.owner_id,
        "name": row.name,
        "display_name": row.display_name,
        "type": row.type,
        "status": row.status,
        "config": dict(row.config_json or {}),
        "credential": {
            "provider": row.credential_provider,
            "ref": row.credential_ref,
            "username": row.credential_username,
        },
        "default_policy": dict(row.default_policy_json or {}),
        "health": dict(row.health_json or {}),
        "last_tested_at": row.last_tested_at,
        "last_used_at": row.last_used_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _row_to_grant(row: ConnectorGrantRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "connector_id": row.connector_id,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "capabilities": list(row.capabilities_json or []),
        "policy_override": dict(row.policy_override_json or {}),
        "expires_at": row.expires_at,
        "created_by": row.created_by,
    }


def _row_to_audit(row: ConnectorAuditLogRow) -> dict[str, Any]:
    return row.to_dict()


class ConnectorRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create_instance(self, values: Mapping[str, Any]) -> dict[str, Any]:
        await self._ensure_name_available(values.get("owner_id"), str(values["name"]))
        credential = dict(values["credential"])
        row = ConnectorInstanceRow(
            id=str(values.get("id") or f"conn_{uuid4().hex}"),
            tenant_id=values.get("tenant_id"),
            owner_id=values.get("owner_id"),
            name=str(values["name"]),
            display_name=values.get("display_name"),
            type=str(values["type"]),
            status=str(values.get("status") or "active"),
            config_json=dict(values.get("config") or {}),
            credential_provider=str(credential.get("provider") or "env"),
            credential_ref=str(credential["ref"]),
            credential_username=credential.get("username"),
            default_policy_json=dict(values.get("default_policy") or {}),
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_instance(row)

    async def _ensure_name_available(self, owner_id: str | None, name: str, *, exclude_id: str | None = None) -> None:
        stmt = select(ConnectorInstanceRow).where(
            ConnectorInstanceRow.name == name,
            ConnectorInstanceRow.deleted_at.is_(None),
            ConnectorInstanceRow.status != "deleted",
        )
        if owner_id is None:
            stmt = stmt.where(ConnectorInstanceRow.owner_id.is_(None))
        else:
            stmt = stmt.where(ConnectorInstanceRow.owner_id == owner_id)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        if any(row.id != exclude_id for row in rows):
            raise ValueError(f"Connector name already exists: {name}")

    async def get_instance(self, connector_id: str, *, owner_id: str | None | object = ...) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(ConnectorInstanceRow, connector_id)
            if row is None or row.deleted_at is not None or row.status == "deleted":
                return None
            if owner_id is not ... and row.owner_id != owner_id:
                return None
            return _row_to_instance(row)

    async def list_instances(self, *, owner_id: str | None | object = ..., type: str | None = None, include_disabled: bool = True) -> list[dict[str, Any]]:
        stmt = select(ConnectorInstanceRow).where(ConnectorInstanceRow.deleted_at.is_(None), ConnectorInstanceRow.status != "deleted")
        if owner_id is not ...:
            stmt = stmt.where(ConnectorInstanceRow.owner_id == owner_id if owner_id is not None else ConnectorInstanceRow.owner_id.is_(None))
        if type:
            stmt = stmt.where(ConnectorInstanceRow.type == type)
        if not include_disabled:
            stmt = stmt.where(ConnectorInstanceRow.status == "active")
        stmt = stmt.order_by(ConnectorInstanceRow.created_at.desc())
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_instance(row) for row in rows]

    async def update_instance(self, connector_id: str, values: Mapping[str, Any], *, owner_id: str | None | object = ...) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(ConnectorInstanceRow, connector_id)
            if row is None or row.deleted_at is not None or row.status == "deleted":
                return None
            if owner_id is not ... and row.owner_id != owner_id:
                return None
            if "name" in values and values["name"] != row.name:
                await self._ensure_name_available(row.owner_id, str(values["name"]), exclude_id=connector_id)
                row.name = str(values["name"])
            for src, dest in (
                ("display_name", "display_name"),
                ("status", "status"),
                ("config", "config_json"),
                ("default_policy", "default_policy_json"),
                ("health", "health_json"),
                ("last_tested_at", "last_tested_at"),
                ("last_used_at", "last_used_at"),
            ):
                if src in values:
                    setattr(row, dest, values[src])
            if "credential" in values:
                credential = dict(values["credential"])
                ref = credential.get("ref")
                if ref is None or ref == "":
                    # A bare provider/username pair with no encrypted blob
                    # is the most common shape of a partial update; the
                    # service layer is responsible for merging the existing
                    # ref in. If it somehow leaks through, fail loudly with
                    # a domain error instead of a generic 500.
                    raise ValueError(
                        "Connector credential update is missing the encrypted ref; "
                        "send the existing ref alongside the new fields to keep the stored secret."
                    )
                row.credential_provider = str(credential.get("provider") or "env")
                row.credential_ref = str(ref)
                # ``username`` may be explicitly nulled (e.g. switching back to
                # env credentials) so we always overwrite.
                row.credential_username = credential.get("username")
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _row_to_instance(row)

    async def soft_delete_instance(self, connector_id: str, *, owner_id: str | None | object = ...) -> bool:
        updated = await self.update_instance(connector_id, {"status": "deleted"}, owner_id=owner_id)
        if updated is None:
            return False
        async with self._sf() as session:
            row = await session.get(ConnectorInstanceRow, connector_id)
            if row is None:
                return False
            row.deleted_at = _now()
            await session.commit()
            return True

    async def create_grant(self, values: Mapping[str, Any]) -> dict[str, Any]:
        row = ConnectorGrantRow(
            id=str(values.get("id") or f"grant_{uuid4().hex}"),
            connector_id=str(values["connector_id"]),
            subject_type=str(values["subject_type"]),
            subject_id=str(values["subject_id"]),
            capabilities_json=list(values.get("capabilities") or []),
            policy_override_json=dict(values.get("policy_override") or {}),
            expires_at=values.get("expires_at"),
            created_by=values.get("created_by"),
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_grant(row)

    async def list_grants(self, connector_id: str) -> list[dict[str, Any]]:
        stmt = select(ConnectorGrantRow).where(ConnectorGrantRow.connector_id == connector_id)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_grant(row) for row in rows]

    async def list_grants_for_connectors(self, connector_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not connector_ids:
            return {}
        stmt = select(ConnectorGrantRow).where(ConnectorGrantRow.connector_id.in_(connector_ids))
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            result.setdefault(row.connector_id, []).append(_row_to_grant(row))
        return result

    async def delete_grant(self, grant_id: str, *, connector_id: str | None = None) -> bool:
        async with self._sf() as session:
            row = await session.get(ConnectorGrantRow, grant_id)
            if row is None:
                return False
            if connector_id is not None and row.connector_id != connector_id:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def update_grant(self, grant_id: str, values: Mapping[str, Any], *, connector_id: str | None = None) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(ConnectorGrantRow, grant_id)
            if row is None:
                return None
            if connector_id is not None and row.connector_id != connector_id:
                return None
            if "capabilities" in values:
                row.capabilities_json = list(values["capabilities"])
            if "policy_override" in values:
                row.policy_override_json = dict(values["policy_override"])
            if "expires_at" in values:
                row.expires_at = values["expires_at"]
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _row_to_grant(row)

    async def put_metadata(self, connector_id: str, resource_type: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        row_id = f"{connector_id}:{resource_type}"
        async with self._sf() as session:
            row = await session.get(ConnectorMetadataCacheRow, row_id)
            if row is None:
                row = ConnectorMetadataCacheRow(id=row_id, connector_id=connector_id, resource_type=resource_type)
                session.add(row)
            row.metadata_json = dict(metadata)
            row.cached_at = _now()
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def get_metadata(self, connector_id: str, resource_type: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(ConnectorMetadataCacheRow, f"{connector_id}:{resource_type}")
            return row.to_dict() if row else None

    async def append_audit(self, values: Mapping[str, Any]) -> dict[str, Any]:
        row = ConnectorAuditLogRow(**dict(values))
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_audit(row)

    async def list_audit(self, *, connector_id: str | None = None, user_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        stmt = select(ConnectorAuditLogRow).order_by(ConnectorAuditLogRow.created_at.desc()).limit(limit)
        if connector_id:
            stmt = stmt.where(ConnectorAuditLogRow.connector_id == connector_id)
        if user_id:
            stmt = stmt.where(ConnectorAuditLogRow.user_id == user_id)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_audit(row) for row in rows]
