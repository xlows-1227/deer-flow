from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.external_idempotency.model import ExternalIdempotencyRow


class IdempotencyConflictError(ValueError):
    pass


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ExternalIdempotencyRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, *, api_key_id: str, idempotency_key: str, request_hash: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(ExternalIdempotencyRow).where(
                        ExternalIdempotencyRow.api_key_id == api_key_id,
                        ExternalIdempotencyRow.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if row is None or _as_utc(row.expires_at) <= datetime.now(UTC):
                return None
            if row.request_hash != request_hash:
                raise IdempotencyConflictError("Idempotency-Key was already used for a different request")
            return row.to_dict()

    async def put(self, values: Mapping[str, Any]) -> dict[str, Any]:
        row = ExternalIdempotencyRow(id=str(values.get("id") or f"idem_{uuid4().hex}"), **{k: v for k, v in values.items() if k != "id"})
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def claim(self, values: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        api_key_id = str(values["api_key_id"])
        idempotency_key = str(values["idempotency_key"])
        request_hash = str(values["request_hash"])
        async with self._sf() as session:
            existing = (
                await session.execute(
                    select(ExternalIdempotencyRow).where(
                        ExternalIdempotencyRow.api_key_id == api_key_id,
                        ExternalIdempotencyRow.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None and _as_utc(existing.expires_at) > datetime.now(UTC):
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError("Idempotency-Key was already used for a different request")
                return existing.to_dict(), False
            if existing is not None:
                await session.delete(existing)
                await session.commit()

        row = ExternalIdempotencyRow(
            id=str(values.get("id") or f"idem_{uuid4().hex}"),
            user_id=str(values["user_id"]),
            api_key_id=api_key_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expires_at=values["expires_at"],
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self.get(
                    api_key_id=row.api_key_id,
                    idempotency_key=row.idempotency_key,
                    request_hash=row.request_hash,
                )
                if existing is None:
                    raise
                return existing, False
            await session.refresh(row)
            return row.to_dict(), True

    async def complete(
        self,
        *,
        api_key_id: str,
        idempotency_key: str,
        run_id: str | None,
        response_status: int,
        response_json: dict[str, Any],
    ) -> None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(ExternalIdempotencyRow).where(
                        ExternalIdempotencyRow.api_key_id == api_key_id,
                        ExternalIdempotencyRow.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one()
            row.run_id = run_id
            row.response_status = response_status
            row.response_json = response_json
            await session.commit()

    async def release(self, *, api_key_id: str, idempotency_key: str) -> None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(ExternalIdempotencyRow).where(
                        ExternalIdempotencyRow.api_key_id == api_key_id,
                        ExternalIdempotencyRow.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if row is not None and row.response_json is None:
                await session.delete(row)
                await session.commit()
