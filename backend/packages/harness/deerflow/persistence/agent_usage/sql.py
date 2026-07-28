"""Atomic SQL quota reservation operations and usage persistence."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.agent_channel.model import AgentChannelRow
from deerflow.persistence.agent_usage.model import (
    AgentQuotaRejectionRow,
    AgentQuotaReservationRow,
    AgentUsageRecordRow,
)
from deerflow.persistence.connector.model import ConnectorAuditLogRow
from deerflow.persistence.published_agent.model import PublishedAgentRow


class EffectiveQuotaLike(Protocol):
    """Quota attributes required by the persistence-side atomic checks."""

    agent_max_concurrent_runs: int
    agent_daily_runs: int
    agent_daily_tokens: int
    agent_inbound_rps: int
    max_concurrent_runs: int
    daily_runs: int
    daily_tokens: int
    max_run_seconds: int
    max_tokens_per_run: int
    inbound_rps: int


class QuotaReservationLimitError(RuntimeError):
    """Persistence-layer signal that an atomic quota check was rejected."""

    def __init__(self, code: str, *, retry_after: int) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = max(1, int(retry_after))


class _SystemSettlementRecoveryScope:
    """Unforgeable marker for the system-only cross-owner outbox scan."""


SYSTEM_SETTLEMENT_RECOVERY_SCOPE = _SystemSettlementRecoveryScope()


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _row_dict(row: AgentQuotaReservationRow) -> dict[str, Any]:
    return row.to_dict()


def _usage_dict(row: AgentUsageRecordRow) -> dict[str, Any]:
    return row.to_dict()


def _cost_microusd(
    row: AgentUsageRecordRow,
    model_costs: Mapping[str, Mapping[str, Any]],
) -> int:
    """Calculate micro-USD from configured USD-per-million-token rates."""
    rates = model_costs.get(row.model)
    if not isinstance(rates, Mapping):
        return 0
    try:
        input_rate = Decimal(str(rates.get("input_usd_per_million_tokens", 0)))
        output_rate = Decimal(str(rates.get("output_usd_per_million_tokens", 0)))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    if input_rate < 0 or output_rate < 0:
        return 0
    # USD / 1M tokens converts directly to micro-USD / token.
    value = input_rate * max(0, row.input_tokens) + output_rate * max(0, row.output_tokens)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _latency_summary(values: list[int]) -> dict[str, int]:
    if not values:
        return {"average": 0, "p95": 0}
    ordered = sorted(max(0, int(value)) for value in values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "average": round(sum(ordered) / len(ordered)),
        "p95": ordered[p95_index],
    }


@dataclass(frozen=True)
class _ScopeLimits:
    max_concurrent_runs: int
    daily_runs: int
    daily_tokens: int
    inbound_rps: int


@dataclass(frozen=True)
class _ScopeUsage:
    pending: tuple[AgentQuotaReservationRow, ...]
    daily_runs: int
    consumed_tokens: int
    recent_runs: int


def _scope_usage(rows: list[AgentQuotaReservationRow], *, one_second_ago: datetime) -> _ScopeUsage:
    return _ScopeUsage(
        pending=tuple(row for row in rows if row.status == "pending"),
        daily_runs=len(rows),
        consumed_tokens=sum(row.reserved_tokens if row.status == "pending" else row.tokens_used for row in rows),
        recent_runs=sum(1 for row in rows if _as_utc(row.created_at) >= one_second_ago),
    )


def _enforce_scope_limits(
    scopes: tuple[tuple[_ScopeUsage, _ScopeLimits], ...],
    *,
    now: datetime,
    next_day: datetime,
    reserved_tokens: int,
) -> None:
    for usage, limits in scopes:
        if len(usage.pending) >= limits.max_concurrent_runs:
            retry_after = min(max(1, math.ceil((_as_utc(row.expires_at) - now).total_seconds())) for row in usage.pending)
            raise QuotaReservationLimitError("max_concurrent_runs_exceeded", retry_after=retry_after)
    for usage, limits in scopes:
        if usage.daily_runs >= limits.daily_runs:
            raise QuotaReservationLimitError("daily_runs_exceeded", retry_after=math.ceil((next_day - now).total_seconds()))
    for usage, limits in scopes:
        if usage.consumed_tokens + reserved_tokens > limits.daily_tokens:
            raise QuotaReservationLimitError("daily_tokens_exceeded", retry_after=math.ceil((next_day - now).total_seconds()))
    for usage, limits in scopes:
        if usage.recent_runs >= limits.inbound_rps:
            raise QuotaReservationLimitError("inbound_rps_exceeded", retry_after=1)


class AgentUsageRepository:
    """Persist reservations and exactly-once usage records for published runs."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        now_fn: Callable[[], datetime] = _now,
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
        """Atomically enforce Agent and credential scopes, then reserve capacity."""
        agent_id = str(values["agent_id"])
        owner_user_id = str(values["owner_user_id"])
        credential_id = str(values["credential_id"])
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
                    AgentQuotaReservationRow.owner_user_id == owner_user_id,
                    AgentQuotaReservationRow.status == "pending",
                    AgentQuotaReservationRow.run_id.is_(None),
                    AgentQuotaReservationRow.expires_at <= now,
                )
                .values(status="released", terminal_status="expired", settled_at=now)
            )
            existing = (
                await session.execute(
                    select(AgentQuotaReservationRow).where(
                        AgentQuotaReservationRow.request_key == request_key,
                        AgentQuotaReservationRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status != "released":
                await session.commit()
                return _row_dict(existing), False
            if existing is not None:
                await session.delete(existing)
                await session.flush()

            agent_rows = (
                (
                    await session.execute(
                        select(AgentQuotaReservationRow).where(
                            AgentQuotaReservationRow.agent_id == agent_id,
                            AgentQuotaReservationRow.owner_user_id == owner_user_id,
                            AgentQuotaReservationRow.created_at >= day_start,
                            AgentQuotaReservationRow.status.in_(("pending", "settled")),
                        )
                    )
                )
                .scalars()
                .all()
            )
            credential_rows = [row for row in agent_rows if row.credential_id == credential_id]
            reserved_tokens = min(
                int(values.get("reserved_tokens") or quota.max_tokens_per_run),
                quota.max_tokens_per_run,
            )
            _enforce_scope_limits(
                (
                    (
                        _scope_usage(agent_rows, one_second_ago=one_second_ago),
                        _ScopeLimits(quota.agent_max_concurrent_runs, quota.agent_daily_runs, quota.agent_daily_tokens, quota.agent_inbound_rps),
                    ),
                    (
                        _scope_usage(credential_rows, one_second_ago=one_second_ago),
                        _ScopeLimits(quota.max_concurrent_runs, quota.daily_runs, quota.daily_tokens, quota.inbound_rps),
                    ),
                ),
                now=now,
                next_day=next_day,
                reserved_tokens=reserved_tokens,
            )

            row = AgentQuotaReservationRow(
                id=str(values.get("id") or f"qres_{uuid4().hex}"),
                request_key=request_key,
                owner_user_id=owner_user_id,
                agent_id=agent_id,
                credential_id=credential_id,
                run_id=str(values["run_id"]) if values.get("run_id") else None,
                reserved_tokens=reserved_tokens,
                expires_at=now + timedelta(seconds=max(1, int(values.get("max_run_seconds") or quota.max_run_seconds)) + 60),
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
                                AgentQuotaReservationRow.request_key == request_key,
                                AgentQuotaReservationRow.owner_user_id == owner_user_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if replay is None:
                        raise
                    return _row_dict(replay), False
            await session.refresh(row)
            return _row_dict(row), True

    async def list_pending_settlements(
        self,
        *,
        recovery_scope: _SystemSettlementRecoveryScope,
    ) -> list[dict[str, Any]]:
        """List durable, Run-bound outbox rows for system recovery passes."""
        if recovery_scope is not SYSTEM_SETTLEMENT_RECOVERY_SCOPE:
            raise PermissionError("cross-owner settlement recovery requires the system scope")
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentQuotaReservationRow)
                        .where(
                            AgentQuotaReservationRow.status == "pending",
                            AgentQuotaReservationRow.run_id.is_not(None),
                        )
                        .order_by(AgentQuotaReservationRow.created_at, AgentQuotaReservationRow.id)
                    )
                )
                .scalars()
                .all()
            )
            return [_row_dict(row) for row in rows]

    async def settle_reservation(
        self,
        reservation_id: str,
        *,
        owner_user_id: str,
        tokens_used: int,
        status: str,
        run_id: str | None,
        usage: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Settle a pending reservation and optionally insert usage atomically."""
        if status not in {"success", "cancelled", "timeout", "failed"}:
            raise ValueError(f"unsupported quota terminal status: {status}")
        if usage is not None and str(usage.get("owner_user_id") or "") != owner_user_id:
            raise ValueError("usage owner_user_id must match reservation scope")
        async with self._sf() as session:
            result = await session.execute(
                update(AgentQuotaReservationRow)
                .where(
                    AgentQuotaReservationRow.id == reservation_id,
                    AgentQuotaReservationRow.owner_user_id == owner_user_id,
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
            if result.rowcount == 1 and usage is not None:
                await self._insert_usage_ignore(session, usage)
            await session.commit()
            row = (
                await session.execute(
                    select(AgentQuotaReservationRow).where(
                        AgentQuotaReservationRow.id == reservation_id,
                        AgentQuotaReservationRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            return (_row_dict(row) if row else None), result.rowcount == 1

    async def _insert_usage_ignore(
        self,
        session: AsyncSession,
        values: Mapping[str, Any],
    ) -> bool:
        payload = {
            "id": str(values.get("id") or f"usage_{uuid4().hex}"),
            **{key: value for key, value in values.items() if key != "id"},
        }
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert

            statement = insert(AgentUsageRecordRow).values(**payload)
            result = await session.execute(statement.on_conflict_do_nothing(index_elements=["run_id"]))
            return result.rowcount == 1
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert

            statement = insert(AgentUsageRecordRow).values(**payload)
            result = await session.execute(statement.on_conflict_do_nothing(index_elements=["run_id"]))
            return result.rowcount == 1
        existing = (await session.execute(select(AgentUsageRecordRow).where(AgentUsageRecordRow.run_id == str(values["run_id"])))).scalar_one_or_none()
        if existing is not None:
            return False
        session.add(AgentUsageRecordRow(**payload))
        await session.flush()
        return True

    async def record_usage(
        self,
        values: Mapping[str, Any],
        *,
        owner_user_id: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Insert one usage row per run, treating duplicates as idempotent."""
        if str(values.get("owner_user_id") or "") != owner_user_id:
            raise ValueError("usage owner_user_id must match repository scope")
        async with self._sf() as session:
            created = await self._insert_usage_ignore(session, values)
            await session.commit()
            row = (
                await session.execute(
                    select(AgentUsageRecordRow).where(
                        AgentUsageRecordRow.run_id == str(values["run_id"]),
                        AgentUsageRecordRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            return (_usage_dict(row) if row is not None else None), created

    async def record_quota_rejection(
        self,
        *,
        owner_user_id: str,
        agent_id: str,
        credential_id: str,
        source: str,
        reason: str,
    ) -> dict[str, Any]:
        """Persist a metadata-only quota denial for owner operations."""
        row = AgentQuotaRejectionRow(
            id=f"qrej_{uuid4().hex}",
            owner_user_id=owner_user_id,
            agent_id=agent_id,
            credential_id=credential_id,
            source=source,
            reason=reason,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def aggregate_daily(
        self,
        *,
        owner_user_id: str,
        agent_id: str,
        since: datetime,
        source: str | None = None,
        credential_id: str | None = None,
        model_costs: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Aggregate owner-scoped usage and sanitized operational signals."""
        async with self._sf() as session:
            statement = select(AgentUsageRecordRow).where(
                AgentUsageRecordRow.owner_user_id == owner_user_id,
                AgentUsageRecordRow.agent_id == agent_id,
                AgentUsageRecordRow.created_at >= since,
            )
            if source is not None:
                statement = statement.where(AgentUsageRecordRow.source == source)
            if credential_id is not None:
                statement = statement.where(AgentUsageRecordRow.credential_id == credential_id)
            rows = (await session.execute(statement)).scalars().all()
            agent = (
                await session.execute(
                    select(PublishedAgentRow).where(
                        PublishedAgentRow.owner_user_id == owner_user_id,
                        PublishedAgentRow.id == agent_id,
                    )
                )
            ).scalar_one_or_none()
            channel_rows = (
                (
                    await session.execute(
                        select(AgentChannelRow).where(
                            AgentChannelRow.agent_id == agent_id,
                        )
                    )
                )
                .scalars()
                .all()
                if agent is not None
                else []
            )
            rejection_rows = (
                (
                    await session.execute(
                        select(AgentQuotaRejectionRow).where(
                            AgentQuotaRejectionRow.owner_user_id == owner_user_id,
                            AgentQuotaRejectionRow.agent_id == agent_id,
                            AgentQuotaRejectionRow.created_at >= since,
                        )
                    )
                )
                .scalars()
                .all()
            )
            connector_rows = (
                (
                    await session.execute(
                        select(ConnectorAuditLogRow).where(
                            ConnectorAuditLogRow.user_id == owner_user_id,
                            ConnectorAuditLogRow.agent_id == agent_id,
                            ConnectorAuditLogRow.created_at >= since,
                        )
                    )
                )
                .scalars()
                .all()
            )

        daily: dict[str, dict[str, Any]] = {}
        pricing = model_costs or {}
        totals = {
            "runs": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_microusd": 0,
        }
        for row in rows:
            day = _as_utc(row.created_at).date().isoformat()
            cost_microusd = _cost_microusd(row, pricing)
            bucket = daily.setdefault(
                day,
                {
                    "date": day,
                    "runs": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_microusd": 0,
                    "statuses": {},
                },
            )
            bucket["runs"] += 1
            bucket["input_tokens"] += row.input_tokens
            bucket["output_tokens"] += row.output_tokens
            bucket["total_tokens"] += row.total_tokens
            bucket["cost_microusd"] += cost_microusd
            bucket["statuses"][row.status] = bucket["statuses"].get(row.status, 0) + 1
            totals["runs"] += 1
            totals["input_tokens"] += row.input_tokens
            totals["output_tokens"] += row.output_tokens
            totals["total_tokens"] += row.total_tokens
            totals["cost_microusd"] += cost_microusd

        current_release_id = str(agent.current_release_id) if agent is not None and agent.current_release_id else None
        current_release_rows = [row for row in rows if current_release_id is not None and row.release_id == current_release_id]
        current_release_errors = sum(row.status != "success" for row in current_release_rows)
        feishu_latencies = [row.event_latency_ms for row in rows if row.source == "feishu" and row.event_latency_ms is not None]
        operations = {
            "agent_status": str(agent.status) if agent is not None else None,
            "agent_active": bool(agent is not None and agent.status == "published"),
            "active_bindings": sum(row.status == "active" for row in channel_rows),
            "unhealthy_bindings": sum(row.health == "unhealthy" for row in channel_rows),
            "quota_rejections": len(rejection_rows),
            "concurrency_saturation": sum(row.reason == "max_concurrent_runs_exceeded" for row in rejection_rows),
            "feishu_event_latency_ms": _latency_summary(feishu_latencies),
            "connector_failures": sum(row.decision == "error" for row in connector_rows),
            "connector_denials": sum(row.decision == "deny" for row in connector_rows),
            "current_release_id": current_release_id,
            "current_release_runs": len(current_release_rows),
            "current_release_errors": current_release_errors,
            "current_release_error_rate": (current_release_errors / len(current_release_rows) if current_release_rows else 0.0),
        }
        return {
            "agent_id": agent_id,
            "days": [daily[key] for key in sorted(daily)],
            "totals": totals,
            "operations": operations,
        }

    async def release_reservation(self, reservation_id: str, *, owner_user_id: str) -> bool:
        """Release only an unbound pending reservation idempotently."""
        async with self._sf() as session:
            result = await session.execute(
                update(AgentQuotaReservationRow)
                .where(
                    AgentQuotaReservationRow.id == reservation_id,
                    AgentQuotaReservationRow.owner_user_id == owner_user_id,
                    AgentQuotaReservationRow.status == "pending",
                    AgentQuotaReservationRow.run_id.is_(None),
                )
                .values(
                    status="released",
                    terminal_status="released",
                    settled_at=self._now(),
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def release_unstarted_reservation(
        self,
        reservation_id: str,
        *,
        owner_user_id: str,
        run_id: str,
    ) -> bool:
        """Release a pre-bound reservation after its exact Run failed to start."""
        if not run_id.strip():
            raise ValueError("run_id must not be empty")
        async with self._sf() as session:
            result = await session.execute(
                update(AgentQuotaReservationRow)
                .where(
                    AgentQuotaReservationRow.id == reservation_id,
                    AgentQuotaReservationRow.owner_user_id == owner_user_id,
                    AgentQuotaReservationRow.status == "pending",
                    AgentQuotaReservationRow.run_id == run_id,
                )
                .values(
                    status="released",
                    terminal_status="unstarted",
                    settled_at=self._now(),
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def get_reservation(self, reservation_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Return one reservation by internal id."""
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(AgentQuotaReservationRow).where(
                        AgentQuotaReservationRow.id == reservation_id,
                        AgentQuotaReservationRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            return _row_dict(row) if row else None

    async def list_reservations(self, *, owner_user_id: str, agent_id: str) -> list[dict[str, Any]]:
        """List reservations for one Agent in creation order."""
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentQuotaReservationRow)
                        .where(
                            AgentQuotaReservationRow.owner_user_id == owner_user_id,
                            AgentQuotaReservationRow.agent_id == agent_id,
                        )
                        .order_by(AgentQuotaReservationRow.created_at, AgentQuotaReservationRow.id)
                    )
                )
                .scalars()
                .all()
            )
            return [_row_dict(row) for row in rows]


__all__ = ["AgentUsageRepository", "QuotaReservationLimitError"]
