"""Layered published-Agent quotas and idempotent reservation facade."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class PlatformQuota:
    """Platform defaults; every value is also a non-bypassable hard cap."""

    max_concurrent_runs_per_agent: int = 8
    max_input_bytes: int = 256 * 1024
    max_run_seconds: int = 600
    max_tokens_per_run: int = 200_000
    inbound_rps: int = 20
    daily_runs_default: int = 1_000
    daily_tokens_default: int = 2_000_000

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"publishing.platform_quota.{name} must be a positive integer")


@dataclass(frozen=True)
class EffectiveQuota:
    """Resolved Agent-wide limits plus credential-specific effective limits."""

    agent_max_concurrent_runs: int
    agent_daily_runs: int
    agent_daily_tokens: int
    agent_inbound_rps: int
    max_concurrent_runs: int
    daily_runs: int
    daily_tokens: int
    max_run_seconds: int
    max_tokens_per_run: int
    max_input_bytes: int
    inbound_rps: int


@dataclass(frozen=True)
class Reservation:
    """One idempotent quota reservation returned to the run orchestrator."""

    id: str
    request_key: str
    agent_id: str
    credential_id: str
    reserved_tokens: int
    status: str


class QuotaExceededError(RuntimeError):
    """Raised when a pre-run reservation would exceed an effective limit."""

    def __init__(self, code: str, *, retry_after: int) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = max(1, int(retry_after))


def _positive_override(values: Mapping[str, Any], key: str, inherited: int) -> int:
    raw = values.get(key)
    if raw is None:
        return inherited
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        raise ValueError(f"quota override {key!r} must be a positive integer")
    return min(inherited, raw)


def resolve_effective_quota(
    platform: PlatformQuota,
    owner_overrides: Mapping[str, Any] | None,
    key_overrides: Mapping[str, Any] | None,
) -> EffectiveQuota:
    """Resolve each limit by inheritance and monotonic minimum."""

    owner = owner_overrides or {}
    key = key_overrides or {}
    hard_limits = {
        "max_concurrent_runs": platform.max_concurrent_runs_per_agent,
        "daily_runs": platform.daily_runs_default,
        "daily_tokens": platform.daily_tokens_default,
        "max_run_seconds": platform.max_run_seconds,
        "max_tokens_per_run": platform.max_tokens_per_run,
        "max_input_bytes": platform.max_input_bytes,
        "inbound_rps": platform.inbound_rps,
    }
    agent_limits: dict[str, int] = {}
    for name, hard_limit in hard_limits.items():
        agent_limits[name] = _positive_override(owner, name, hard_limit)
    resolved = {name: _positive_override(key, name, agent_limit) for name, agent_limit in agent_limits.items()}
    agent_limits["max_tokens_per_run"] = min(
        agent_limits["max_tokens_per_run"],
        agent_limits["daily_tokens"],
    )
    resolved["max_tokens_per_run"] = min(resolved["max_tokens_per_run"], resolved["daily_tokens"])
    return EffectiveQuota(
        agent_max_concurrent_runs=agent_limits["max_concurrent_runs"],
        agent_daily_runs=agent_limits["daily_runs"],
        agent_daily_tokens=agent_limits["daily_tokens"],
        agent_inbound_rps=agent_limits["inbound_rps"],
        **resolved,
    )


class AgentKeyRepoLike(Protocol):
    """Minimal Agent-key repository contract used by quota resolution."""

    async def get(self, agent_id: str, key_id: str, *, owner_user_id: str) -> dict[str, Any] | None: ...


class PublishedQuotaResolver:
    """Resolve Release(owner) and credential overrides under platform caps."""

    def __init__(self, platform: PlatformQuota, key_repo: AgentKeyRepoLike) -> None:
        self._platform = platform
        self._keys = key_repo

    async def resolve(
        self,
        *,
        owner_user_id: str,
        release: dict[str, Any],
        credential_id: str,
    ) -> EffectiveQuota:
        """Resolve Agent-wide owner limits and credential-specific tightening."""
        key = await self._keys.get(str(release["agent_id"]), credential_id, owner_user_id=owner_user_id)
        key_overrides = key.get("quota_overrides") if key is not None else {}
        return resolve_effective_quota(
            self._platform,
            release.get("quota_overrides") or {},
            key_overrides or {},
        )


class UsageRepoLike(Protocol):
    """Atomic reservation repository contract used by ``QuotaLedger``."""

    async def reserve_quota(
        self,
        values: Mapping[str, Any],
        *,
        quota: EffectiveQuota,
    ) -> tuple[dict[str, Any], bool]: ...

    async def settle_reservation(
        self,
        reservation_id: str,
        *,
        owner_user_id: str,
        tokens_used: int,
        status: str,
        run_id: str | None,
        usage: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, bool]: ...

    async def release_reservation(self, reservation_id: str, *, owner_user_id: str) -> bool: ...


class QuotaLedger:
    """Quota policy facade over the atomic SQL reservation repository."""

    def __init__(self, repository: UsageRepoLike) -> None:
        self._repository = repository

    async def reserve(self, context: Any, *, request_key: str) -> Reservation:
        """Reserve quota for one trusted context and idempotency scope."""
        from deerflow.persistence.agent_usage.sql import QuotaReservationLimitError

        quota = context.effective_quota
        if not isinstance(quota, EffectiveQuota):
            raise TypeError("PublishedAgentContext.effective_quota must be EffectiveQuota")
        try:
            row, _created = await self._repository.reserve_quota(
                {
                    "owner_user_id": context.owner_user_id,
                    "agent_id": context.agent_id,
                    "credential_id": context.credential_id,
                    "request_key": request_key,
                    "reserved_tokens": quota.max_tokens_per_run,
                    "max_run_seconds": quota.max_run_seconds,
                },
                quota=quota,
            )
        except QuotaReservationLimitError as exc:
            raise QuotaExceededError(exc.code, retry_after=exc.retry_after) from exc
        return Reservation(
            id=str(row["id"]),
            request_key=str(row["request_key"]),
            agent_id=str(row["agent_id"]),
            credential_id=str(row["credential_id"]),
            reserved_tokens=int(row["reserved_tokens"]),
            status=str(row["status"]),
        )

    async def settle(
        self,
        reservation_id: str,
        *,
        owner_user_id: str,
        tokens_used: int,
        status: str,
        run_id: str | None = None,
        usage: Mapping[str, Any] | None = None,
    ) -> bool:
        """Settle a reservation once and optionally persist terminal usage."""
        _row, changed = await self._repository.settle_reservation(
            reservation_id,
            owner_user_id=owner_user_id,
            tokens_used=max(0, int(tokens_used)),
            status=status,
            run_id=run_id,
            usage=usage,
        )
        return changed

    async def release(self, reservation_id: str, *, owner_user_id: str) -> bool:
        """Release a reservation that never reached a running Run."""
        return await self._repository.release_reservation(reservation_id, owner_user_id=owner_user_id)


__all__ = [
    "EffectiveQuota",
    "PlatformQuota",
    "PublishedQuotaResolver",
    "QuotaExceededError",
    "QuotaLedger",
    "Reservation",
    "resolve_effective_quota",
]
