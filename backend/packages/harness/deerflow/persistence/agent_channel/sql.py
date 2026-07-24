"""Owner-scoped repository for Published-Agent channel bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from deerflow.persistence.agent_channel.model import AgentChannelRow, AgentChannelSecretIngestRow
from deerflow.persistence.published_agent.model import PublishedAgentRow


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive reflection to the UTC DB contract."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ActiveAgentChannelConflictError(RuntimeError):
    """Raised when an Agent already has an active binding of this type."""


class AgentChannelSecretCleanupPendingError(RuntimeError):
    """Raised when a previous credential cleanup must converge first."""


class _SystemChannelSupervisorScope:
    pass


SYSTEM_CHANNEL_SUPERVISOR_SCOPE = _SystemChannelSupervisorScope()


@dataclass(frozen=True)
class RuntimeClaimReconciliation:
    """Durable result of serializing one abandoned startup claim."""

    row: dict[str, Any] | None
    exact_token_released: bool
    failure_health_current: bool


def _to_dict(row: AgentChannelRow, *, owner_user_id: str | None = None) -> dict[str, Any]:
    value = row.to_dict()
    if owner_user_id is not None:
        value["owner_user_id"] = owner_user_id
    return value


class AgentChannelRepository:
    """CRUD for channel bindings with explicit owner isolation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Bind the repository to the application's async session factory."""
        self._sf = session_factory

    @staticmethod
    def _owned_query(
        agent_id: str,
        binding_id: str,
        owner_user_id: str,
    ) -> Select[tuple[AgentChannelRow]]:
        return (
            select(AgentChannelRow)
            .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
            .where(
                AgentChannelRow.id == binding_id,
                AgentChannelRow.agent_id == agent_id,
                PublishedAgentRow.owner_user_id == owner_user_id,
            )
        )

    @staticmethod
    def _clear_locked_runtime_lease(
        row: AgentChannelRow,
        *,
        now: datetime,
    ) -> None:
        """Apply the shared fenced lease-release state to an already locked row."""
        row.runtime_lease_token = None
        row.runtime_lease_expires_at = None
        row.runtime_generation += 1
        row.health_revision = 0
        row.updated_at = now

    async def owns_agent(self, agent_id: str, *, owner_user_id: str) -> bool:
        """Return whether ``owner_user_id`` owns the stable Agent identity."""
        async with self._sf() as session:
            value = (
                await session.execute(
                    select(PublishedAgentRow.id).where(
                        PublishedAgentRow.id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            return value is not None

    async def reserve_secret_ingest(
        self,
        *,
        agent_id: str,
        binding_id: str,
        owner_user_id: str,
        secret_ref: str,
        defer_seconds: float = 120.0,
    ) -> dict[str, Any] | None:
        """Create a database owner before ciphertext is written."""
        async with self._sf() as session:
            owner = (
                await session.execute(
                    select(PublishedAgentRow.id).where(
                        PublishedAgentRow.id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if owner is None:
                return None
            now = _now()
            row = AgentChannelSecretIngestRow(
                secret_ref=secret_ref,
                agent_id=agent_id,
                binding_id=binding_id,
                owner_user_id=owner_user_id,
                state="reserved",
                not_before=now + timedelta(seconds=max(0.0, defer_seconds)),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            return row.to_dict()

    async def begin_secret_ingest_write(
        self,
        secret_ref: str,
        *,
        agent_id: str,
        binding_id: str,
        owner_user_id: str,
        writer_token: str,
        lease_seconds: float = 30.0,
    ) -> dict[str, Any] | None:
        """CAS a reservation into a fenced writer generation."""
        async with self._sf() as session:
            row = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            if row is None or row.state != "reserved" or row.agent_id != agent_id or row.binding_id != binding_id or row.owner_user_id != owner_user_id:
                return None
            now = _now()
            row.state = "writing"
            row.writer_token = writer_token
            row.writer_generation += 1
            row.writer_lease_expires_at = now + timedelta(seconds=max(0.1, lease_seconds))
            row.updated_at = now
            await session.commit()
            return row.to_dict()

    async def renew_secret_ingest_write(
        self,
        secret_ref: str,
        *,
        writer_token: str,
        writer_generation: int,
        lease_seconds: float = 30.0,
    ) -> bool:
        """Heartbeat only the matching active writer generation."""
        async with self._sf() as session:
            row = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            if row is None or row.state != "writing" or row.writer_token != writer_token or row.writer_generation != writer_generation:
                return False
            now = _now()
            row.writer_lease_expires_at = now + timedelta(seconds=max(0.1, lease_seconds))
            row.updated_at = now
            await session.commit()
            return True

    async def complete_secret_ingest_write(
        self,
        secret_ref: str,
        *,
        writer_token: str,
        writer_generation: int,
    ) -> dict[str, Any] | None:
        """Publish ciphertext for atomic binding transfer by matching writer CAS."""
        async with self._sf() as session:
            row = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            if row is None or row.state != "writing" or row.writer_token != writer_token or row.writer_generation != writer_generation:
                return None
            row.state = "ready"
            row.writer_token = None
            row.writer_lease_expires_at = None
            row.updated_at = _now()
            await session.commit()
            return row.to_dict()

    async def fail_secret_ingest_write(
        self,
        secret_ref: str,
        *,
        writer_token: str,
        writer_generation: int,
    ) -> bool:
        """Make a completed/failed local write immediately cleanup-eligible."""
        async with self._sf() as session:
            row = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            if row is None or row.state != "writing" or row.writer_token != writer_token or row.writer_generation != writer_generation:
                return False
            now = _now()
            row.state = "ready"
            row.writer_token = None
            row.writer_lease_expires_at = None
            row.not_before = now
            row.updated_at = now
            await session.commit()
            return True

    async def list_secret_ingests_due(self, *, supervisor_scope: object) -> list[dict[str, Any]]:
        """List transferable/reserved work or an expired writer/cleanup claim."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            now = _now()
            rows = (
                (
                    await session.execute(
                        select(AgentChannelSecretIngestRow)
                        .where(
                            ((AgentChannelSecretIngestRow.state.in_({"reserved", "ready"})) & (AgentChannelSecretIngestRow.not_before <= now))
                            | ((AgentChannelSecretIngestRow.state == "writing") & (AgentChannelSecretIngestRow.writer_lease_expires_at <= now))
                            | ((AgentChannelSecretIngestRow.state == "cleanup_claimed") & (AgentChannelSecretIngestRow.claim_expires_at <= now)),
                        )
                        .order_by(AgentChannelSecretIngestRow.not_before, AgentChannelSecretIngestRow.secret_ref)
                    )
                )
                .scalars()
                .all()
            )
            return [row.to_dict() for row in rows]

    async def claim_secret_ingest_cleanup(
        self,
        secret_ref: str,
        *,
        claim_token: str,
        supervisor_scope: object,
        lease_seconds: float = 30.0,
    ) -> dict[str, Any] | None:
        """CAS one due ingest into janitor ownership."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            row = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            now = _now()
            if row is None:
                return None
            if row.state == "cleanup_claimed" and (row.claim_expires_at is None or _utc(row.claim_expires_at) > now):
                return None
            due = (
                row.state in {"reserved", "ready"}
                and _utc(row.not_before) <= now
                or row.state == "writing"
                and row.writer_lease_expires_at is not None
                and _utc(row.writer_lease_expires_at) <= now
                or row.state == "cleanup_claimed"
                and row.claim_expires_at is not None
                and _utc(row.claim_expires_at) <= now
            )
            if not due:
                return None
            row.state = "cleanup_claimed"
            row.claim_token = claim_token
            row.claim_expires_at = now + timedelta(seconds=max(0.1, lease_seconds))
            row.updated_at = now
            await session.commit()
            return row.to_dict()

    async def claim_owned_secret_ingest_cleanup(
        self,
        secret_ref: str,
        *,
        agent_id: str,
        binding_id: str,
        owner_user_id: str,
        claim_token: str,
        lease_seconds: float = 30.0,
    ) -> dict[str, Any] | None:
        """Let an owner request immediate cleanup only before DB transfer wins."""
        async with self._sf() as session:
            row = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            if row is None or row.state not in {"reserved", "ready"} or row.agent_id != agent_id or row.binding_id != binding_id or row.owner_user_id != owner_user_id:
                return None
            now = _now()
            row.state = "cleanup_claimed"
            row.claim_token = claim_token
            row.claim_expires_at = now + timedelta(seconds=max(0.1, lease_seconds))
            row.not_before = now
            row.updated_at = now
            await session.commit()
            return row.to_dict()

    async def complete_owned_secret_ingest_cleanup(
        self,
        secret_ref: str,
        *,
        agent_id: str,
        binding_id: str,
        owner_user_id: str,
        claim_token: str,
    ) -> bool:
        """Acknowledge owner cleanup only for its matching ingest claim."""
        async with self._sf() as session:
            row = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            if row is None or row.state != "cleanup_claimed" or row.claim_token != claim_token or row.agent_id != agent_id or row.binding_id != binding_id or row.owner_user_id != owner_user_id:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def complete_secret_ingest_cleanup(
        self,
        secret_ref: str,
        *,
        claim_token: str,
        supervisor_scope: object,
    ) -> bool:
        """Acknowledge ciphertext erase only for the matching janitor claim."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            row = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            if row is None or row.state != "cleanup_claimed" or row.claim_token != claim_token:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def create(
        self,
        *,
        agent_id: str,
        owner_user_id: str,
        app_id: str,
        secret_ref: str,
        channel_type: str = "feishu",
        connection_mode: str = "websocket",
    ) -> dict[str, Any] | None:
        """Create an inactive binding when the caller owns the Agent."""
        async with self._sf() as session:
            owner = (
                await session.execute(
                    select(PublishedAgentRow.id).where(
                        PublishedAgentRow.id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if owner is None:
                return None
            row = AgentChannelRow(
                id=f"ach_{uuid4().hex}",
                agent_id=agent_id,
                channel_type=channel_type,
                app_id=app_id,
                secret_ref=secret_ref,
                connection_mode=connection_mode,
                status="inactive",
                health="unknown",
            )
            session.add(row)
            await session.commit()
            return _to_dict(row)

    async def create_from_secret_ingest(
        self,
        *,
        agent_id: str,
        binding_id: str,
        owner_user_id: str,
        app_id: str,
        secret_ref: str,
        channel_type: str = "feishu",
        connection_mode: str = "websocket",
    ) -> dict[str, Any] | None:
        """Atomically transfer a reserved secret ingest into a new binding."""
        async with self._sf() as session:
            ingest = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            if ingest is None or ingest.state not in {"ready", "pending"} or ingest.agent_id != agent_id or ingest.binding_id != binding_id or ingest.owner_user_id != owner_user_id:
                return None
            row = AgentChannelRow(
                id=binding_id,
                agent_id=agent_id,
                channel_type=channel_type,
                app_id=app_id,
                secret_ref=secret_ref,
                connection_mode=connection_mode,
                status="inactive",
                health="unknown",
            )
            session.add(row)
            await session.delete(ingest)
            await session.commit()
            return _to_dict(row)

    async def get(self, agent_id: str, binding_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Return one binding only when the caller owns its stable Agent."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id))).scalar_one_or_none()
            return _to_dict(row) if row is not None else None

    async def list_by_agent(self, agent_id: str, *, owner_user_id: str) -> list[dict[str, Any]]:
        """List bindings for one owner-scoped Agent without exposing secrets."""
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentChannelRow)
                        .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
                        .where(
                            AgentChannelRow.agent_id == agent_id,
                            PublishedAgentRow.owner_user_id == owner_user_id,
                        )
                        .order_by(AgentChannelRow.created_at, AgentChannelRow.id)
                    )
                )
                .scalars()
                .all()
            )
            return [_to_dict(row) for row in rows]

    async def list_active(self, *, supervisor_scope: object) -> list[dict[str, Any]]:
        """Return all desired-active bindings to the trusted Supervisor only."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(AgentChannelRow, PublishedAgentRow.owner_user_id)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
                    .where(AgentChannelRow.status == "active")
                    .order_by(AgentChannelRow.created_at, AgentChannelRow.id)
                )
            ).all()
            return [_to_dict(row, owner_user_id=str(owner_user_id)) for row, owner_user_id in rows]

    async def list_deleting(self, *, supervisor_scope: object) -> list[dict[str, Any]]:
        """Return durable deletion tombstones to the trusted Supervisor only."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(AgentChannelRow, PublishedAgentRow.owner_user_id)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
                    .where(AgentChannelRow.status == "deleting")
                    .order_by(AgentChannelRow.updated_at, AgentChannelRow.id)
                )
            ).all()
            return [_to_dict(row, owner_user_id=str(owner_user_id)) for row, owner_user_id in rows]

    async def list_runtime_stop_requested(self, *, supervisor_scope: object) -> list[dict[str, Any]]:
        """Return active rows whose runtime must quiesce before deactivation."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(AgentChannelRow, PublishedAgentRow.owner_user_id)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
                    .where(
                        AgentChannelRow.status == "active",
                        AgentChannelRow.runtime_stop_requested.is_(True),
                    )
                    .order_by(AgentChannelRow.updated_at, AgentChannelRow.id)
                )
            ).all()
            return [_to_dict(row, owner_user_id=str(owner_user_id)) for row, owner_user_id in rows]

    async def get_for_supervisor(self, binding_id: str, *, supervisor_scope: object) -> dict[str, Any] | None:
        """Resolve one binding across owners for the trusted Supervisor only."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            result = (await session.execute(select(AgentChannelRow, PublishedAgentRow.owner_user_id).join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id).where(AgentChannelRow.id == binding_id))).one_or_none()
            if result is None:
                return None
            row, owner_user_id = result
            return _to_dict(row, owner_user_id=str(owner_user_id))

    async def activate(self, agent_id: str, binding_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Set desired state active under an owner lock and enforce uniqueness."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.status == "deleting" or row.runtime_stop_requested or row.runtime_lease_token is not None:
                return None
            row.status = "active"
            row.runtime_stop_requested = False
            row.runtime_lease_token = None
            row.runtime_lease_expires_at = None
            row.runtime_generation += 1
            row.health = "unknown"
            row.health_detail = None
            row.health_revision = 0
            row.last_started_at = _now()
            row.updated_at = _now()
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ActiveAgentChannelConflictError("Agent already has an active channel binding") from exc
            return _to_dict(row)

    async def deactivate(self, agent_id: str, binding_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Persist inactive desired state after the Supervisor confirms stop."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.status == "deleting" or row.runtime_lease_token is not None:
                return None
            row.status = "inactive"
            row.runtime_stop_requested = False
            row.health = "unknown"
            row.health_detail = None
            row.health_revision = 0
            row.runtime_lease_token = None
            row.runtime_lease_expires_at = None
            row.runtime_generation += 1
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def request_runtime_stop(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        lease_seconds: float = 15.0,
    ) -> dict[str, Any] | None:
        """Durably revoke an active runtime without releasing its fencing token."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.status == "deleting":
                return None
            row.runtime_stop_requested = True
            if row.runtime_lease_token is not None:
                row.runtime_lease_expires_at = _now() + timedelta(seconds=max(0.1, lease_seconds))
            row.health = "unknown"
            row.health_detail = "Runtime stop is pending"
            row.health_revision = 0
            row.runtime_generation += 1
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def claim_runtime(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        lease_token: str,
        restore_deleting: bool = False,
        lease_seconds: float = 15.0,
    ) -> dict[str, Any] | None:
        """Acquire a provisional, expiring runtime lease without publishing recovery."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return None
            if row.status == "deleting":
                if not restore_deleting or row.delete_previous_status != "active":
                    return None
            elif row.status != "active" or row.runtime_stop_requested:
                return None
            now = _now()
            # A timestamp is a heartbeat diagnostic, not proof that the old
            # transport exited. Only its matching explicit release may clear
            # the fencing token.
            if row.runtime_lease_token not in {None, lease_token}:
                return None
            row.runtime_lease_token = lease_token
            row.runtime_lease_expires_at = now + timedelta(seconds=max(0.1, lease_seconds))
            row.runtime_generation += 1
            row.health_revision = 0
            row.updated_at = now
            await session.commit()
            return _to_dict(row)

    async def confirm_runtime(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        lease_token: str,
        restore_deleting: bool = False,
        lease_seconds: float = 15.0,
    ) -> dict[str, Any] | None:
        """Publish a registered runtime only while its provisional lease still owns the row."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            now = _now()
            if row is None or row.runtime_lease_token != lease_token or row.runtime_lease_expires_at is None or _utc(row.runtime_lease_expires_at) <= now:
                return None
            if row.status == "deleting":
                if not restore_deleting or row.delete_previous_status != "active":
                    return None
                row.status = "active"
                row.delete_previous_status = None
            elif row.status != "active" or row.runtime_stop_requested or restore_deleting:
                return None
            row.runtime_lease_expires_at = now + timedelta(seconds=max(0.1, lease_seconds))
            row.updated_at = now
            await session.commit()
            return _to_dict(row)

    async def renew_runtime(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        lease_token: str,
        lease_seconds: float = 15.0,
    ) -> bool:
        """Heartbeat one active runtime claim and observe durable revocation."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            now = _now()
            if row is None or row.status != "active" or row.runtime_stop_requested or row.runtime_lease_token != lease_token or row.runtime_lease_expires_at is None or _utc(row.runtime_lease_expires_at) <= now:
                return False
            row.runtime_lease_expires_at = now + timedelta(seconds=max(0.1, lease_seconds))
            row.updated_at = now
            await session.commit()
            return True

    async def renew_quiescing_runtime(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        lease_token: str,
        lease_seconds: float = 15.0,
    ) -> bool:
        """Keep fencing ownership while a revoked transport is still stopping."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.runtime_lease_token != lease_token or not (row.status == "deleting" or row.runtime_stop_requested):
                return False
            row.runtime_lease_expires_at = _now() + timedelta(seconds=max(0.1, lease_seconds))
            row.updated_at = _now()
            await session.commit()
            return True

    async def release_runtime(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        lease_token: str,
        expected_runtime_generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Release or idempotently confirm only the caller's exact lease generation."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return None
            if row.runtime_lease_token != lease_token:
                if expected_runtime_generation is not None and row.runtime_lease_token is None and row.runtime_generation == expected_runtime_generation + 1:
                    return _to_dict(row)
                return None
            if expected_runtime_generation is not None and row.runtime_generation != expected_runtime_generation:
                return None
            self._clear_locked_runtime_lease(row, now=_now())
            await session.commit()
            return _to_dict(row)

    async def reconcile_runtime_claim(
        self,
        binding_id: str,
        *,
        lease_token: str,
        supervisor_scope: object,
        failure_health: str | None = None,
        failure_detail: str | None = None,
        expected_claim_generation: int | None = None,
    ) -> RuntimeClaimReconciliation:
        """Serialize with an ambiguous claim and settle its failure epoch."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope is required")
        async with self._sf() as session:
            row = (await session.execute(select(AgentChannelRow).where(AgentChannelRow.id == binding_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return RuntimeClaimReconciliation(
                    row=None,
                    exact_token_released=False,
                    failure_health_current=False,
                )
            if row.runtime_lease_token != lease_token:
                current = _to_dict(row)
                safe_failure_detail = failure_detail[:512] if failure_detail else None
                return RuntimeClaimReconciliation(
                    row=current,
                    exact_token_released=False,
                    failure_health_current=(
                        row.runtime_lease_token is None
                        and failure_health is not None
                        and (expected_claim_generation is None or row.runtime_generation == expected_claim_generation + 1)
                        and row.health == failure_health
                        and row.health_detail == safe_failure_detail
                    ),
                )
            claim_generation = row.runtime_generation
            self._clear_locked_runtime_lease(row, now=_now())
            failure_health_current = failure_health is not None and row.status == "active" and not row.runtime_stop_requested and (expected_claim_generation is None or claim_generation == expected_claim_generation)
            if failure_health_current:
                row.health = failure_health
                row.health_detail = failure_detail[:512] if failure_detail else None
                row.health_revision = 1
            await session.commit()
            return RuntimeClaimReconciliation(
                row=_to_dict(row),
                exact_token_released=True,
                failure_health_current=failure_health_current,
            )

    async def recover_orphaned_runtime_leases(
        self,
        *,
        supervisor_scope: object,
    ) -> int:
        """Clear crash-orphaned tokens after the caller owns the process leader fence."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope is required")
        async with self._sf() as session:
            rows = (await session.execute(select(AgentChannelRow).where(AgentChannelRow.runtime_lease_token.is_not(None)).with_for_update())).scalars()
            recovered = 0
            now = _now()
            for row in rows:
                self._clear_locked_runtime_lease(row, now=now)
                recovered += 1
            if recovered:
                await session.commit()
            return recovered

    async def update_credentials(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        app_id: str,
        secret_ref: str,
    ) -> dict[str, Any] | None:
        """Atomically replace one owner's encrypted credential reference."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.status == "deleting":
                return None
            row.app_id = app_id
            row.secret_ref = secret_ref
            # Credentials define the authority behind a health observation.
            # Advance the durable epoch so a probe started with the previous
            # secret cannot publish after this replacement commits.
            row.runtime_generation += 1
            row.health = "unknown"
            row.health_detail = None
            row.health_revision = 0
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def stage_secret_cleanup(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        secret_ref: str,
        reason: str = "rotation_candidate",
        defer_seconds: float = 120.0,
    ) -> dict[str, Any] | None:
        """Durably own a newly-created credential before rotation mutates the row."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.status == "deleting":
                return None
            if row.secret_cleanup_ref is not None:
                raise AgentChannelSecretCleanupPendingError("A previous Feishu credential cleanup is pending")
            row.secret_cleanup_ref = secret_ref
            row.secret_cleanup_reason = reason
            row.secret_cleanup_not_before = _now() + timedelta(seconds=max(0.0, defer_seconds))
            row.rotation_previous_secret_ref = row.secret_ref
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def stage_secret_cleanup_from_ingest(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        secret_ref: str,
        reason: str = "rotation_candidate",
        defer_seconds: float = 120.0,
    ) -> dict[str, Any] | None:
        """Atomically transfer a pending ingest into the binding cleanup state."""
        async with self._sf() as session:
            ingest = (await session.execute(select(AgentChannelSecretIngestRow).where(AgentChannelSecretIngestRow.secret_ref == secret_ref).with_for_update())).scalar_one_or_none()
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if ingest is None or ingest.state not in {"ready", "pending"} or ingest.agent_id != agent_id or ingest.binding_id != binding_id or ingest.owner_user_id != owner_user_id or row is None or row.status == "deleting":
                return None
            if row.secret_cleanup_ref is not None:
                raise AgentChannelSecretCleanupPendingError("A previous Feishu credential cleanup is pending")
            row.secret_cleanup_ref = secret_ref
            row.secret_cleanup_reason = reason
            row.secret_cleanup_not_before = _now() + timedelta(seconds=max(0.0, defer_seconds))
            row.rotation_previous_secret_ref = row.secret_ref
            row.updated_at = _now()
            await session.delete(ingest)
            await session.commit()
            return _to_dict(row)

    async def replace_secret_cleanup(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        expected_ref: str,
        secret_ref: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """Transition a staged candidate to rollback/superseded deletion work."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.secret_cleanup_ref != expected_ref:
                return None
            row.secret_cleanup_ref = secret_ref
            row.secret_cleanup_reason = reason
            row.secret_cleanup_not_before = _now()
            row.rotation_previous_secret_ref = None
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def clear_secret_cleanup(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        secret_ref: str,
    ) -> bool:
        """Acknowledge erasure only when the durable outbox ref still matches."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.secret_cleanup_ref != secret_ref:
                return False
            row.secret_cleanup_ref = None
            row.secret_cleanup_reason = None
            row.secret_cleanup_not_before = None
            row.rotation_previous_secret_ref = None
            row.updated_at = _now()
            await session.commit()
            return True

    async def recover_staged_secret_cleanup(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
    ) -> dict[str, Any] | None:
        """Resolve a crashed rotation candidate from the row's current ref."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.secret_cleanup_reason != "rotation_candidate" or row.secret_cleanup_ref is None:
                return _to_dict(row) if row is not None else None
            if row.secret_ref == row.secret_cleanup_ref:
                row.secret_cleanup_ref = row.rotation_previous_secret_ref
                row.secret_cleanup_reason = "rotation_superseded" if row.rotation_previous_secret_ref else None
            else:
                row.secret_cleanup_reason = "rotation_rollback"
            row.rotation_previous_secret_ref = None
            row.secret_cleanup_not_before = _now() if row.secret_cleanup_ref else None
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def list_secret_cleanup_due(self, *, supervisor_scope: object) -> list[dict[str, Any]]:
        """Return due encrypted-secret erasure work to the trusted Supervisor."""
        if supervisor_scope is not SYSTEM_CHANNEL_SUPERVISOR_SCOPE:
            raise PermissionError("system channel supervisor scope required")
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(AgentChannelRow, PublishedAgentRow.owner_user_id)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentChannelRow.agent_id)
                    .where(
                        AgentChannelRow.secret_cleanup_ref.is_not(None),
                        AgentChannelRow.secret_cleanup_not_before <= _now(),
                    )
                    .order_by(AgentChannelRow.secret_cleanup_not_before, AgentChannelRow.id)
                )
            ).all()
            return [_to_dict(row, owner_user_id=str(owner_user_id)) for row, owner_user_id in rows]

    async def update_health(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        health: str,
        detail: str | None = None,
        expected_runtime_generation: int,
        expected_runtime_lease_token: str | None,
        health_revision: int,
    ) -> dict[str, Any] | None:
        """Persist only a newer observation from the runtime generation that produced it."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.runtime_generation != expected_runtime_generation or row.runtime_lease_token != expected_runtime_lease_token or health_revision <= row.health_revision:
                return None
            row.health = health
            row.health_detail = detail[:512] if detail else None
            row.health_revision = health_revision
            row.updated_at = _now()
            await session.commit()
            return _to_dict(row)

    async def mark_deleting(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        lease_seconds: float = 15.0,
    ) -> dict[str, Any] | None:
        """Persist a retryable deletion tombstone without losing the secret ref."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return None
            if row.status != "deleting":
                row.delete_previous_status = row.status
                row.status = "deleting"
                row.runtime_stop_requested = True
                if row.runtime_lease_token is not None:
                    row.runtime_lease_expires_at = _now() + timedelta(seconds=max(0.1, lease_seconds))
                row.runtime_generation += 1
                row.health = "unknown"
                row.health_detail = "Deletion is pending"
                row.health_revision = 0
                row.updated_at = _now()
                await session.commit()
            return _to_dict(row)

    async def restore_deleting(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
    ) -> dict[str, Any] | None:
        """Restore desired state when quiescing is rejected before secret erasure."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None:
                return None
            if row.status == "deleting":
                row.status = row.delete_previous_status or "inactive"
                row.delete_previous_status = None
                row.runtime_stop_requested = False
                row.runtime_lease_token = None
                row.runtime_lease_expires_at = None
                row.runtime_generation += 1
                row.health = "unknown"
                row.health_detail = None
                row.health_revision = 0
                row.updated_at = _now()
                await session.commit()
            return _to_dict(row)

    async def delete(self, agent_id: str, binding_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Physically remove one durable deletion tombstone."""
        async with self._sf() as session:
            row = (await session.execute(self._owned_query(agent_id, binding_id, owner_user_id).with_for_update())).scalar_one_or_none()
            if row is None or row.status != "deleting" or row.runtime_lease_token is not None:
                return None
            value = _to_dict(row)
            await session.delete(row)
            await session.commit()
            return value


__all__ = [
    "ActiveAgentChannelConflictError",
    "AgentChannelSecretCleanupPendingError",
    "AgentChannelRepository",
    "SYSTEM_CHANNEL_SUPERVISOR_SCOPE",
]
