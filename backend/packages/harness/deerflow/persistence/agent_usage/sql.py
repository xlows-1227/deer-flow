"""Atomic SQL quota reservation operations and usage persistence."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.agent_usage.model import AgentQuotaReservationRow


class EffectiveQuotaLike(Protocol):
    max_concurrent_runs: int
    daily_runs: int
    daily_tokens: int
    max_run_seconds: int
    max_tokens_per_run: int
    inbound_rps: int


class QuotaReservationLimitError(RuntimeError):
    def __init__(self, code: str, *, retry_after: int) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = max(1, int(retry_after))


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _row_dict(row: AgentQuotaReservationRow) -> dict[str, Any]:
    return row.to_dict()


class AgentUsageRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        now_fn=_now,
    ) -> None:
        self._sf = session_factory
        self._now = now_fn

    async def _lock_scope(self, session: AsyncSession, agent_id: str) -> None:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            await session.execute(text("BEGIN IMMEDIATE"))
        elif dialect == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
                {"scope": f"agent-quota:{agent_id}"},
            )

    async def reserve_quota(
        self,
        values: Mapping[str, Any],
        *,
        quota: EffectiveQuotaLike,
    ) -> tuple[dict[str, Any], bool]:
        agent_id = str(values["agent_id"])
        request_key = str(values["request_key"])
        if len(request_key) > 128:
            raise ValueError("quota request_key must not exceed 128 characters")
        now = self._now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day_start + timedelta(days=1)
        one_second_ago = now - timedelta(seconds=1)

        async with self._sf() as session:
            await self._lock_scope(session, agent_id)
            await session.execute(
                update(AgentQuotaReservationRow)
                .where(
                    AgentQuotaReservationRow.agent_id == agent_id,
                    AgentQuotaReservationRow.status == "pending",
                    AgentQuotaReservationRow.expires_at <= now,
                )
                .values(status="released", terminal_status="expired", settled_at=now)
            )
            existing = (
                await session.execute(
                    select(AgentQuotaReservationRow).where(
                        AgentQuotaReservationRow.request_key == request_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status != "released":
                await session.commit()
                return _row_dict(existing), False
            if existing is not None:
                await session.delete(existing)
                await session.flush()

            rows = (
                await session.execute(
                    select(AgentQuotaReservationRow).where(
                        AgentQuotaReservationRow.agent_id == agent_id,
                        AgentQuotaReservationRow.created_at >= day_start,
                        AgentQuotaReservationRow.status.in_(("pending", "settled")),
                    )
                )
            ).scalars().all()
            pending = [row for row in rows if row.status == "pending"]
            if len(pending) >= quota.max_concurrent_runs:
                retry_after = min(
                    max(1, math.ceil((_as_utc(row.expires_at) - now).total_seconds()))
                    for row in pending
                )
                raise QuotaReservationLimitError(
                    "max_concurrent_runs_exceeded",
                    retry_after=retry_after,
                )
            if len(rows) >= quota.daily_runs:
                raise QuotaReservationLimitError(
                    "daily_runs_exceeded",
                    retry_after=math.ceil((next_day - now).total_seconds()),
                )
            consumed_tokens = sum(
                row.reserved_tokens if row.status == "pending" else row.tokens_used
                for row in rows
            )
            reserved_tokens = min(
                int(values.get("reserved_tokens") or quota.max_tokens_per_run),
                quota.max_tokens_per_run,
            )
            if consumed_tokens + reserved_tokens > quota.daily_tokens:
                raise QuotaReservationLimitError(
                    "daily_tokens_exceeded",
                    retry_after=math.ceil((next_day - now).total_seconds()),
                )
            recent_count = sum(1 for row in rows if _as_utc(row.created_at) >= one_second_ago)
            if recent_count >= quota.inbound_rps:
                raise QuotaReservationLimitError("inbound_rps_exceeded", retry_after=1)

            row = AgentQuotaReservationRow(
                id=str(values.get("id") or f"qres_{uuid4().hex}"),
                request_key=request_key,
                owner_user_id=str(values["owner_user_id"]),
                agent_id=agent_id,
                credential_id=str(values["credential_id"]),
                reserved_tokens=reserved_tokens,
                expires_at=now
                + timedelta(seconds=max(1, int(values.get("max_run_seconds") or quota.max_run_seconds)) + 60),
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                async with self._sf() as retry_session:
                    replay = (
                        await retry_session.execute(
                            select(AgentQuotaReservationRow).where(
                                AgentQuotaReservationRow.request_key == request_key
                            )
                        )
                    ).scalar_one_or_none()
                    if replay is None:
                        raise
                    return _row_dict(replay), False
            await session.refresh(row)
            return _row_dict(row), True

    async def settle_reservation(
        self,
        reservation_id: str,
        *,
        tokens_used: int,
        status: str,
        run_id: str | None,
    ) -> tuple[dict[str, Any] | None, bool]:
        if status not in {"success", "cancelled", "timeout", "failed"}:
            raise ValueError(f"unsupported quota terminal status: {status}")
        async with self._sf() as session:
            result = await session.execute(
                update(AgentQuotaReservationRow)
                .where(
                    AgentQuotaReservationRow.id == reservation_id,
                    AgentQuotaReservationRow.status == "pending",
                )
                .values(
                    status="settled",
                    terminal_status=status,
                    tokens_used=max(0, int(tokens_used)),
                    run_id=run_id,
                    settled_at=self._now(),
                )
            )
            await session.commit()
            row = await session.get(AgentQuotaReservationRow, reservation_id)
            return (_row_dict(row) if row else None), result.rowcount == 1

    async def release_reservation(self, reservation_id: str) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                update(AgentQuotaReservationRow)
                .where(
                    AgentQuotaReservationRow.id == reservation_id,
                    AgentQuotaReservationRow.status == "pending",
                )
                .values(
                    status="released",
                    terminal_status="released",
                    settled_at=self._now(),
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def get_reservation(self, reservation_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(AgentQuotaReservationRow, reservation_id)
            return _row_dict(row) if row else None

    async def list_reservations(self, *, agent_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(AgentQuotaReservationRow)
                    .where(AgentQuotaReservationRow.agent_id == agent_id)
                    .order_by(AgentQuotaReservationRow.created_at, AgentQuotaReservationRow.id)
                )
            ).scalars().all()
            return [_row_dict(row) for row in rows]


__all__ = ["AgentUsageRepository", "QuotaReservationLimitError"]
