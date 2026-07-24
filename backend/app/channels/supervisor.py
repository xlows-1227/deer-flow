"""Database-driven lifecycle supervisor for Published-Agent Feishu bindings."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

import httpx
from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from app.channels.base import Channel
from app.channels.contracts import EventDeduplicator
from app.channels.message_bus import MessageBus
from deerflow.config.paths import get_paths
from deerflow.persistence.agent_channel import (
    SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
    AgentChannelRepository,
    RuntimeClaimReconciliation,
)
from deerflow.publishing.feishu_credentials import decode_feishu_credentials
from deerflow.publishing.secret_store import SecretStore

logger = logging.getLogger(__name__)

RUNTIME_LEASE_TTL_SECONDS = 15.0
RUNTIME_LEASE_HEARTBEAT_SECONDS = 5.0
RUNTIME_LEASE_RELEASE_WAIT_SECONDS = 20.0
# Opening a Feishu websocket includes a readiness handshake that may consume the
# full 15-second provider timeout.  Keep that provisional ownership independent
# from the shorter steady-state heartbeat lease so a valid slow start cannot be
# mistaken for a crashed gateway before confirmation and health projection.
RUNTIME_STARTUP_LEASE_TTL_SECONDS = 30.0
RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS = 25.0
RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS = 2.0
RUNTIME_STARTUP_FAILURE_PROJECTION_TIMEOUT_SECONDS = 2.0
RUNTIME_STOP_TIMEOUT_SECONDS = 10.0
RUNTIME_LATE_CLAIM_RETRY_SECONDS = 0.05
RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS = 20.0

ConnectionTester = Callable[[str, str], Awaitable[tuple[bool, str]]]
RuntimeHealthCallback = Callable[[bool, str | None], Awaitable[None]]


class ChannelFactory(Protocol):
    """Build one isolated dynamic channel from decrypted credentials."""

    def __call__(
        self,
        bus: MessageBus,
        *,
        app_id: str,
        app_secret: str,
        verification_token: str,
        encrypt_key: str,
        binding_id: str,
        agent_id: str,
        event_deduplicator: EventDeduplicator | None = None,
        runtime_error_callback: Callable[[str], Awaitable[None]] | None = None,
        runtime_health_callback: RuntimeHealthCallback | None = None,
    ) -> Channel: ...


class DynamicChannelRegistry(Protocol):
    """Registry seam exposed by the legacy ChannelService."""

    def register_dynamic_channel(self, channel: Channel) -> None: ...

    def unregister_dynamic_channel(self, channel_name: str) -> None: ...


class RuntimeLeaderFence(Protocol):
    """Process-liveness authority for the v1 single-supervisor deployment."""

    async def acquire(self) -> bool: ...

    async def release(self) -> None: ...


class _FileRuntimeLeaderFence:
    """OS-released fence shared through the deployment's durable base directory."""

    def __init__(self) -> None:
        # Acquisition/release run through the shared asyncio worker pool, so
        # filelock state must not be bound to whichever thread executes first.
        self._lock = FileLock(
            str(get_paths().base_dir / "published-feishu-supervisor.lock"),
            thread_local=False,
        )
        self._held = False

    async def acquire(self) -> bool:
        if self._held:
            return True
        acquire_task = asyncio.create_task(asyncio.to_thread(self._lock.acquire, timeout=2.0))
        try:
            await asyncio.shield(acquire_task)
        except asyncio.CancelledError:
            # Cancelling ``to_thread`` only abandons its asyncio future; the
            # worker may still acquire the OS lock later.  Drain it and release
            # any late acquisition before preserving cancellation semantics.
            while not acquire_task.done():
                try:
                    await asyncio.shield(acquire_task)
                except asyncio.CancelledError:
                    continue
            try:
                acquire_task.result()
            except FileLockTimeout:
                pass
            except Exception:
                logger.exception("Cancelled Feishu leader acquisition failed while draining")
            else:
                release_task = asyncio.create_task(asyncio.to_thread(self._lock.release))
                while not release_task.done():
                    try:
                        await asyncio.shield(release_task)
                    except asyncio.CancelledError:
                        continue
                release_task.result()
            raise
        except FileLockTimeout:
            return False
        self._held = True
        return True

    async def release(self) -> None:
        if not self._held:
            return
        await asyncio.to_thread(self._lock.release)
        self._held = False


class BindingNotFoundError(LookupError):
    """Raised when a binding disappears before a lifecycle operation."""


class BindingCleanupPendingError(RuntimeError):
    """Raised when durable attachment work prevents physical binding deletion."""


class BindingStartError(RuntimeError):
    """Raised when a lifecycle operation requires a ready runtime but start fails."""


class SupervisorShuttingDownError(RuntimeError):
    """Raised when runtime admission is closed during process shutdown."""


class _StaleHealthProjectionError(RuntimeError):
    """Raised when a health writer no longer owns the durable generation."""


@dataclass(frozen=True)
class BindingHealth:
    """Safe per-binding runtime health exposed to owner operations."""

    binding_id: str
    agent_id: str
    health: str
    detail: str | None
    running: bool


@dataclass
class _RunningChannel:
    channel: Channel
    owner_user_id: str
    agent_id: str
    generation: object
    lease_token: str
    runtime_generation: int
    start_task: asyncio.Task[None] | None = None
    startup_lease_task: asyncio.Task[bool] | None = None
    stop_task: asyncio.Task[None] | None = None
    release_task: asyncio.Task[dict[str, Any] | None] | None = None
    lease_task: asyncio.Task[None] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    quiescing: bool = False


class _StartupPolicy(Enum):
    """Closed startup policies; lifecycle callers cannot invent combinations."""

    EXPLICIT = "explicit"
    EXPLICIT_STRICT = "explicit_strict"
    RELOAD = "reload"

    @property
    def strict(self) -> bool:
        return self is self.EXPLICIT_STRICT

    @property
    def reread_active(self) -> bool:
        return self is self.RELOAD

    @property
    def isolate_failures(self) -> bool:
        return self is self.RELOAD

    @property
    def skip_not_found(self) -> bool:
        return self is self.RELOAD

    @classmethod
    def explicit(cls, *, strict: bool) -> _StartupPolicy:
        return cls.EXPLICIT_STRICT if strict else cls.EXPLICIT


_STARTUP_RELOAD_POLICY = _StartupPolicy.RELOAD


@dataclass
class _StartupAttempt:
    """Durable fence captured before any health result can be detached."""

    runtime_generation: int
    lease_token: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> _StartupAttempt:
        return cls(
            runtime_generation=int(row["runtime_generation"]),
            lease_token=row.get("runtime_lease_token"),
        )

    def capture_row(self, row: dict[str, Any]) -> None:
        self.runtime_generation = int(row["runtime_generation"])
        self.lease_token = row.get("runtime_lease_token")

    def capture_running(self, running: _RunningChannel) -> None:
        self.runtime_generation = running.runtime_generation
        self.lease_token = running.lease_token


@dataclass
class _BindingLockEntry:
    lock: asyncio.Lock
    users: int = 0
    retired: bool = False


def _default_channel_factory(
    bus: MessageBus,
    *,
    app_id: str,
    app_secret: str,
    verification_token: str,
    encrypt_key: str,
    binding_id: str,
    agent_id: str,
    event_deduplicator: EventDeduplicator | None = None,
    runtime_error_callback: Callable[[str], Awaitable[None]] | None = None,
    runtime_health_callback: RuntimeHealthCallback | None = None,
) -> Channel:
    from app.channels.feishu import FeishuChannel

    return FeishuChannel(
        bus=bus,
        app_id=app_id,
        app_secret=app_secret,
        verification_token=verification_token,
        encrypt_key=encrypt_key,
        binding_id=binding_id,
        agent_id=agent_id,
        event_deduplicator=event_deduplicator,
        runtime_error_callback=runtime_error_callback,
        runtime_health_callback=runtime_health_callback,
    )


async def _default_connection_tester(app_id: str, app_secret: str) -> tuple[bool, str]:
    """Probe Feishu credentials without returning provider payloads or secrets."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        response.raise_for_status()
        payload = response.json()
    if payload.get("code") == 0:
        return True, "connected"
    return False, "credentials rejected"


class FeishuSupervisor:
    """Maintain one isolated Feishu channel instance per active DB binding."""

    def __init__(
        self,
        repository: AgentChannelRepository,
        secret_store: SecretStore,
        bus: MessageBus,
        *,
        channel_factory: ChannelFactory | None = None,
        connection_tester: ConnectionTester | None = None,
        channel_registry: DynamicChannelRegistry | None = None,
        event_deduplicator: EventDeduplicator | None = None,
        runtime_leader_fence: RuntimeLeaderFence | None = None,
    ) -> None:
        """Configure isolated binding lifecycles and trusted system seams."""
        self._repository = repository
        self._secret_store = secret_store
        self._bus = bus
        self._channel_factory = channel_factory or _default_channel_factory
        self._connection_tester = connection_tester or _default_connection_tester
        self._channel_registry = channel_registry
        self._event_deduplicator = event_deduplicator
        self._runtime_leader_fence = runtime_leader_fence or _FileRuntimeLeaderFence()
        self._runtime_leader_owned = False
        from app.channels.feishu import start_published_attachment_backlog_scanner

        start_published_attachment_backlog_scanner()
        self._running: dict[str, _RunningChannel] = {}
        self._health: dict[str, BindingHealth] = {}
        self._health_revisions: dict[str, int] = {}
        self._binding_locks: dict[str, _BindingLockEntry] = {}
        self._late_runtime_claim_tasks: set[asyncio.Task[dict[str, Any] | None]] = set()
        self._late_runtime_release_tasks: set[asyncio.Task[None]] = set()
        self._cleanup_janitor_task: asyncio.Task[None] | None = None
        self._shutting_down = False
        self._shutdown_complete = False
        self._shutdown_lock = asyncio.Lock()

    @asynccontextmanager
    async def _binding_lifecycle(self, binding_id: str) -> AsyncIterator[_BindingLockEntry]:
        """Serialize one binding and reclaim retired locks after all waiters exit."""
        entry = self._binding_locks.get(binding_id)
        if entry is None:
            entry = _BindingLockEntry(lock=asyncio.Lock())
            self._binding_locks[binding_id] = entry
        entry.users += 1
        try:
            async with entry.lock:
                yield entry
        finally:
            entry.users -= 1
            if entry.retired and entry.users == 0 and self._binding_locks.get(binding_id) is entry:
                self._binding_locks.pop(binding_id, None)

    @property
    def running_binding_ids(self) -> tuple[str, ...]:
        """Return process-local bindings whose ready handshake completed."""
        return tuple(sorted(binding_id for binding_id, running in self._running.items() if self._is_serving(running)))

    @property
    def owned_binding_ids(self) -> tuple[str, ...]:
        """Return every process-local fencing owner, including quiescing ones."""
        return tuple(sorted(self._running))

    @staticmethod
    def _is_serving(running: _RunningChannel | None) -> bool:
        """Separate transport service readiness from retained fence ownership."""
        return bool(running is not None and running.channel.is_running and not running.quiescing)

    def health(self) -> dict[str, BindingHealth]:
        """Return a snapshot of redacted process-local binding health."""
        return dict(self._health)

    def _reserve_health_revision(self, row: dict[str, Any]) -> int:
        """Allocate a process-local order that is also fenced durably."""
        binding_id = str(row["id"])
        revision = max(self._health_revisions.get(binding_id, 0), int(row.get("health_revision", 0))) + 1
        self._health_revisions[binding_id] = revision
        return revision

    async def _binding(self, binding_id: str) -> dict[str, Any]:
        row = await self._repository.get_for_supervisor(
            binding_id,
            supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
        )
        if row is None:
            raise BindingNotFoundError(binding_id)
        return row

    async def _record_health(
        self,
        row: dict[str, Any],
        *,
        health: str,
        detail: str | None,
        running: bool,
        expected_runtime_generation: int | None = None,
        expected_runtime_lease_token: str | None = None,
    ) -> BindingHealth:
        safe_detail = detail[:512] if detail else None
        if expected_runtime_generation is None:
            expected_runtime_generation = int(row["runtime_generation"])
            expected_runtime_lease_token = row.get("runtime_lease_token")
        health_revision = self._reserve_health_revision(row)
        persisted = await self._repository.update_health(
            str(row["agent_id"]),
            str(row["id"]),
            owner_user_id=str(row["owner_user_id"]),
            health=health,
            detail=safe_detail,
            expected_runtime_generation=expected_runtime_generation,
            expected_runtime_lease_token=expected_runtime_lease_token,
            health_revision=health_revision,
        )
        if persisted is None:
            raise _StaleHealthProjectionError(str(row["id"]))
        value = BindingHealth(
            binding_id=str(row["id"]),
            agent_id=str(row["agent_id"]),
            health=health,
            detail=safe_detail,
            running=running,
        )
        if self._health_revisions.get(value.binding_id) != health_revision:
            raise _StaleHealthProjectionError(value.binding_id)
        self._health[value.binding_id] = value
        return value

    def _ensure_runtime_admission(self) -> None:
        if self._shutting_down:
            raise SupervisorShuttingDownError("Feishu supervisor is shutting down")

    @staticmethod
    def _ensure_not_deleting(row: dict[str, Any]) -> None:
        if row["status"] == "deleting":
            raise BindingCleanupPendingError(str(row["id"]))
        if row.get("runtime_stop_requested"):
            raise BindingCleanupPendingError(str(row["id"]))

    async def _start_row(
        self,
        row: dict[str, Any],
        *,
        strict: bool = False,
    ) -> BindingHealth:
        """Converge one runtime within a complete per-binding startup deadline."""
        health = await self._converge_startup(
            row,
            policy=_StartupPolicy.explicit(strict=strict),
        )
        if health is None:
            raise BindingNotFoundError(str(row["id"]))
        return health

    async def _converge_startup(
        self,
        row: dict[str, Any],
        *,
        policy: _StartupPolicy,
    ) -> BindingHealth | None:
        """Apply one shared deadline and failure policy to every startup entry point."""
        attempt = _StartupAttempt.from_row(row)
        startup_deadline = asyncio.get_running_loop().time() + RUNTIME_STARTUP_CONVERGENCE_TIMEOUT_SECONDS
        try:
            async with asyncio.timeout_at(startup_deadline):
                current = await self._binding(str(row["id"])) if policy.reread_active else row
                attempt.capture_row(current)
                if policy.reread_active and (current["status"] != "active" or current.get("runtime_stop_requested")):
                    return None
                return await self._start_row_once(
                    current,
                    strict=policy.strict,
                    attempt=attempt,
                    startup_deadline=startup_deadline,
                )
        except asyncio.CancelledError:
            raise
        except BindingNotFoundError:
            if policy.skip_not_found:
                return None
            raise
        except TimeoutError as exc:
            logger.error(
                "Published Feishu binding startup convergence timed out",
                extra={"binding_id": row["id"], "agent_id": row["agent_id"]},
            )
            health = await self._record_startup_failure(row, attempt=attempt)
            if policy.strict:
                raise BindingStartError("Feishu channel startup convergence timed out") from exc
            return health
        except Exception as exc:
            if not policy.isolate_failures:
                raise
            logger.error(
                "Published Feishu binding failed during startup convergence",
                extra={"binding_id": row["id"], "error_class": type(exc).__name__},
            )
            return await self._record_startup_failure(row, attempt=attempt)

    async def _record_startup_failure(self, row: dict[str, Any], *, attempt: _StartupAttempt) -> BindingHealth:
        """Project unhealthy without allowing another database stall to block peers."""
        binding_id = str(row["id"])
        running = self._running.get(binding_id)
        fallback = BindingHealth(
            binding_id=binding_id,
            agent_id=str(row["agent_id"]),
            health="unhealthy",
            detail="Feishu channel failed to start",
            running=bool(running and running.channel.is_running and not running.quiescing),
        )
        projection_task = asyncio.create_task(
            self._record_health(
                row,
                health=fallback.health,
                detail=fallback.detail,
                running=fallback.running,
                expected_runtime_generation=attempt.runtime_generation,
                expected_runtime_lease_token=attempt.lease_token,
            )
        )
        done, _pending = await asyncio.wait(
            {projection_task},
            timeout=RUNTIME_STARTUP_FAILURE_PROJECTION_TIMEOUT_SECONDS,
        )
        if projection_task not in done:
            projection_task.cancel()
            projection_task.add_done_callback(self._consume_detached_task)
            logger.error(
                "Feishu startup failure health projection timed out",
                extra={"binding_id": binding_id},
            )
            return fallback
        outcome = (await asyncio.gather(projection_task, return_exceptions=True))[0]
        if isinstance(outcome, BindingHealth):
            return outcome
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, BaseException):
            logger.exception(
                "Failed to persist Feishu startup convergence health",
                exc_info=(type(outcome), outcome, outcome.__traceback__),
                extra={"binding_id": binding_id},
            )
        return fallback

    @staticmethod
    def _consume_detached_task(task: asyncio.Task[Any]) -> None:
        """Retrieve a detached cleanup result so late completion cannot emit warnings."""
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    async def _claim_runtime_before_deadline(
        self,
        row: dict[str, Any],
        *,
        lease_token: str,
        startup_deadline: float,
    ) -> dict[str, Any] | None:
        """Bound claim latency without letting a cancellation-resistant call block peers."""
        claim_task = asyncio.create_task(
            self._repository.claim_runtime(
                str(row["agent_id"]),
                str(row["id"]),
                owner_user_id=str(row["owner_user_id"]),
                lease_token=lease_token,
                lease_seconds=RUNTIME_STARTUP_LEASE_TTL_SECONDS,
            )
        )
        timeout = max(0.0, startup_deadline - asyncio.get_running_loop().time())
        try:
            done, _pending = await asyncio.wait({claim_task}, timeout=timeout)
        except asyncio.CancelledError:
            self._detach_runtime_claim(claim_task, row=row, lease_token=lease_token)
            raise
        if claim_task not in done:
            self._detach_runtime_claim(claim_task, row=row, lease_token=lease_token)
            raise TimeoutError("Feishu runtime lease claim timed out")
        try:
            return await claim_task
        except asyncio.CancelledError:
            self._detach_runtime_claim(claim_task, row=row, lease_token=lease_token)
            raise
        except Exception:
            # A completed client task does not prove the server rolled back:
            # commit may have succeeded before its acknowledgement failed.
            self._detach_runtime_claim(claim_task, row=row, lease_token=lease_token)
            raise

    def _detach_runtime_claim(
        self,
        task: asyncio.Task[dict[str, Any] | None],
        *,
        row: dict[str, Any],
        lease_token: str,
    ) -> None:
        """Retain one ambiguous claim until its exact token is reconciled."""
        self._late_runtime_claim_tasks.add(task)

        def settled(completed: asyncio.Task[dict[str, Any] | None]) -> None:
            self._late_runtime_claim_tasks.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                logger.warning(
                    "Outcome-ambiguous Feishu runtime claim ended cancelled; reconciling its token",
                    extra={"binding_id": row["id"]},
                )
            except Exception:
                logger.exception(
                    "Outcome-ambiguous Feishu runtime claim failed; reconciling its token",
                    extra={"binding_id": row["id"]},
                )
            release_task = asyncio.create_task(
                self._release_late_runtime_claim(
                    row,
                    lease_token=lease_token,
                )
            )
            self._late_runtime_release_tasks.add(release_task)
            release_task.add_done_callback(self._consume_late_runtime_release)

        task.add_done_callback(settled)

    async def _release_late_runtime_claim(
        self,
        row: dict[str, Any],
        *,
        lease_token: str,
    ) -> None:
        """Retry an ambiguous claim until this exact token no longer owns the row."""
        while True:
            try:
                reconciled = await self._repository.reconcile_runtime_claim(
                    str(row["id"]),
                    lease_token=lease_token,
                    supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
                    failure_health="unhealthy",
                    failure_detail="Feishu channel failed to start",
                    expected_claim_generation=int(row["runtime_generation"]) + 1,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Failed to reconcile detached Feishu runtime claim",
                    extra={"binding_id": row["id"]},
                )
                await asyncio.sleep(RUNTIME_LATE_CLAIM_RETRY_SECONDS)
                continue
            try:
                if reconciled.failure_health_current:
                    await self._publish_reconciled_startup_failure(reconciled)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Failed to publish reconciled Feishu startup failure",
                    extra={"binding_id": row["id"]},
                )
            await asyncio.sleep(RUNTIME_LATE_CLAIM_RETRY_SECONDS)

    async def _publish_reconciled_startup_failure(
        self,
        reconciliation: RuntimeClaimReconciliation,
    ) -> None:
        """Publish an atomic failure projection only while its epoch is current."""
        projected = reconciliation.row
        if projected is None:
            return
        binding_id = str(projected["id"])
        async with self._binding_lifecycle(binding_id):
            current = await self._binding(binding_id)
            fingerprint = (
                "runtime_generation",
                "runtime_lease_token",
                "health_revision",
                "health",
                "health_detail",
            )
            if any(current.get(field) != projected.get(field) for field in fingerprint):
                return
            revision = int(current["health_revision"])
            self._health_revisions[binding_id] = max(
                self._health_revisions.get(binding_id, 0),
                revision,
            )
            self._health[binding_id] = BindingHealth(
                binding_id=binding_id,
                agent_id=str(current["agent_id"]),
                health=str(current["health"]),
                detail=current.get("health_detail"),
                running=self._is_serving(self._running.get(binding_id)),
            )

    def _consume_late_runtime_release(self, task: asyncio.Task[None]) -> None:
        self._late_runtime_release_tasks.discard(task)
        self._consume_detached_task(task)

    async def _drain_late_runtime_claim_ownership(self, *, deadline: float) -> None:
        """Wait within the shutdown budget for every detached claim and retry owner."""
        while True:
            tasks: set[asyncio.Task[Any]] = set(self._late_runtime_claim_tasks) | set(self._late_runtime_release_tasks)
            if not tasks:
                # A completed claim's callback schedules its release owner on
                # the next loop turn. Recheck after callbacks have run.
                await asyncio.sleep(0)
                if not self._late_runtime_claim_tasks and not self._late_runtime_release_tasks:
                    return
                continue
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("late runtime claim cleanup did not converge")
            _done, pending = await asyncio.wait(tasks, timeout=remaining)
            if pending:
                raise TimeoutError("late runtime claim cleanup did not converge")
            await asyncio.sleep(0)

    async def _drain_quiescing_runtime_ownership(self, *, deadline: float) -> None:
        """Wait for every retained transport/release owner within shutdown's budget."""
        while self._running:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("quiescing runtime cleanup did not converge")
            cleanup_tasks: set[asyncio.Task[None]] = set()
            for binding_id, running in tuple(self._running.items()):
                self._ensure_cleanup_retry(binding_id, running)
                if running.cleanup_task is not None:
                    cleanup_tasks.add(running.cleanup_task)
            if not cleanup_tasks:
                raise TimeoutError("quiescing runtime cleanup has no retry owner")
            done, _pending = await asyncio.wait(
                cleanup_tasks,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError("quiescing runtime cleanup did not converge")
            await asyncio.sleep(0)

    async def _start_row_once(
        self,
        row: dict[str, Any],
        *,
        strict: bool = False,
        attempt: _StartupAttempt,
        startup_deadline: float,
    ) -> BindingHealth:
        binding_id = str(row["id"])
        existing = self._running.get(binding_id)
        if existing is not None and existing.channel.is_running and not existing.quiescing:
            cleanup_pending = getattr(existing.channel, "attachment_cleanup_healthy", True) is False
            return await self._record_health(
                row,
                health="unhealthy" if cleanup_pending else "healthy",
                detail="Attachment cleanup recovery is pending" if cleanup_pending else None,
                running=True,
            )
        if existing is not None:
            self._enter_quiescing(binding_id, existing)
            await self._replace_lease_monitor_with_cleanup_retry(binding_id, existing)
            raise BindingCleanupPendingError(binding_id)

        channel: Channel | None = None
        generation = object()
        lease_token = uuid4().hex
        lease_claimed = False
        registered = False
        try:
            credentials = decode_feishu_credentials(await self._secret_store.get(str(row["secret_ref"])))
            channel_kwargs = {
                "app_id": str(row["app_id"]),
                "app_secret": credentials.app_secret,
                "verification_token": credentials.verification_token,
                "encrypt_key": credentials.encrypt_key,
                "binding_id": binding_id,
                "agent_id": str(row["agent_id"]),
                "runtime_error_callback": lambda detail: self._handle_runtime_error(
                    binding_id,
                    detail,
                    generation=generation,
                ),
                "runtime_health_callback": lambda healthy, detail: self._handle_runtime_health(
                    binding_id,
                    healthy,
                    detail,
                    generation=generation,
                ),
            }
            if self._event_deduplicator is not None:
                channel_kwargs["event_deduplicator"] = self._event_deduplicator
            channel = self._channel_factory(self._bus, **channel_kwargs)
            self._ensure_runtime_admission()
            current = await self._binding(binding_id)
            self._ensure_not_deleting(current)
            claimed = await self._claim_runtime_before_deadline(
                row,
                lease_token=lease_token,
                startup_deadline=startup_deadline,
            )
            if claimed is None:
                latest = await self._repository.get_for_supervisor(
                    binding_id,
                    supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
                )
                if latest is None:
                    raise BindingNotFoundError(binding_id)
                if latest["status"] == "deleting":
                    raise BindingCleanupPendingError(binding_id)
                raise BindingStartError("Feishu runtime lease could not be claimed")
            lease_claimed = True
            attempt.capture_row(claimed)
            running = _RunningChannel(
                channel=channel,
                owner_user_id=str(row["owner_user_id"]),
                agent_id=str(row["agent_id"]),
                generation=generation,
                lease_token=lease_token,
                runtime_generation=int(claimed["runtime_generation"]),
            )
            self._running[binding_id] = running
            claimed_current = await self._binding(binding_id)
            self._ensure_not_deleting(claimed_current)
            if claimed_current.get("runtime_lease_token") != lease_token:
                raise BindingStartError("Feishu runtime lease was revoked before transport start")
            # A transport may open only after durable fencing ownership exists.
            # Keeping the provisional runtime in ``_running`` also gives a
            # failed ``start()`` a process-local retry owner until ``stop()``
            # has actually returned.
            await self._start_channel_with_provisional_lease(
                row,
                running=running,
            )
            if not channel.is_running:
                raise RuntimeError("channel did not enter running state")
            self._ensure_runtime_admission()
            confirmed = await self._repository.confirm_runtime(
                str(row["agent_id"]),
                binding_id,
                owner_user_id=str(row["owner_user_id"]),
                lease_token=lease_token,
                lease_seconds=RUNTIME_LEASE_TTL_SECONDS,
            )
            if confirmed is None:
                latest = await self._repository.get_for_supervisor(
                    binding_id,
                    supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
                )
                if latest is None:
                    raise BindingNotFoundError(binding_id)
                if latest["status"] == "deleting":
                    raise BindingCleanupPendingError(binding_id)
                raise BindingStartError("Feishu runtime lease was revoked before registration completed")
            attempt.capture_row(confirmed)
            running.runtime_generation = int(confirmed["runtime_generation"])
            if self._channel_registry is not None:
                self._channel_registry.register_dynamic_channel(channel)
                registered = True
            cleanup_pending = getattr(channel, "attachment_cleanup_healthy", True) is False
            health = await self._record_health(
                {**row, **confirmed},
                health="unhealthy" if cleanup_pending else "healthy",
                detail="Attachment cleanup recovery is pending" if cleanup_pending else None,
                running=True,
            )
            running.lease_task = asyncio.create_task(
                self._monitor_runtime_lease(
                    binding_id,
                    lease_token=lease_token,
                    generation=generation,
                )
            )
            logger.info(
                "Published Feishu binding started",
                extra={"binding_id": binding_id, "agent_id": row["agent_id"]},
            )
            return health
        except asyncio.CancelledError:
            await self._discard_unpublished_runtime(
                row,
                channel=channel,
                lease_token=lease_token,
                lease_claimed=lease_claimed,
                registered=registered,
                attempt=attempt,
            )
            raise
        except SupervisorShuttingDownError:
            await self._discard_unpublished_runtime(
                row,
                channel=channel,
                lease_token=lease_token,
                lease_claimed=lease_claimed,
                registered=registered,
                attempt=attempt,
            )
            raise
        except (BindingNotFoundError, BindingCleanupPendingError, BindingStartError):
            await self._discard_unpublished_runtime(
                row,
                channel=channel,
                lease_token=lease_token,
                lease_claimed=lease_claimed,
                registered=registered,
                attempt=attempt,
            )
            raise
        except Exception as exc:
            await self._discard_unpublished_runtime(
                row,
                channel=channel,
                lease_token=lease_token,
                lease_claimed=lease_claimed,
                registered=registered,
                attempt=attempt,
            )
            logger.error(
                "Published Feishu binding failed to start",
                extra={
                    "binding_id": binding_id,
                    "agent_id": row["agent_id"],
                    "error_class": type(exc).__name__,
                },
            )
            health = await self._record_health(
                row,
                health="unhealthy",
                detail="Feishu channel failed to start",
                running=False,
                expected_runtime_generation=attempt.runtime_generation,
                expected_runtime_lease_token=attempt.lease_token,
            )
            if strict:
                raise BindingStartError("Feishu channel failed to start") from exc
            return health

    async def _start_channel_with_provisional_lease(
        self,
        row: dict[str, Any],
        *,
        running: _RunningChannel,
    ) -> None:
        """Open one transport while renewing its still-provisional fencing token."""
        start_task = asyncio.create_task(running.channel.start())
        renewal_task = asyncio.create_task(
            self._renew_provisional_runtime_lease(
                row,
                lease_token=running.lease_token,
            )
        )
        running.start_task = start_task
        running.startup_lease_task = renewal_task
        try:
            done, _pending = await asyncio.wait(
                {start_task, renewal_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if renewal_task in done and not renewal_task.result():
                start_task.cancel()
                raise BindingStartError("Feishu provisional runtime lease was lost during transport start")
            await start_task
        except BaseException:
            if not start_task.done():
                start_task.cancel()
            renewal_task.cancel()
            if start_task.done():
                await asyncio.gather(start_task, return_exceptions=True)
                running.start_task = None
            raise
        running.start_task = None
        if not await self._cancel_and_drain_task(
            renewal_task,
            timeout=RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS,
        ):
            raise BindingStartError("Feishu provisional runtime lease task did not stop")
        running.startup_lease_task = None

    @staticmethod
    async def _cancel_and_drain_task(task: asyncio.Task[Any], *, timeout: float) -> bool:
        """Request task cancellation without letting a non-cooperative task hold its caller."""
        if not task.done():
            task.cancel()
            done, _pending = await asyncio.wait({task}, timeout=max(0.0, timeout))
            if task not in done:
                return False
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def _drain_runtime_task(
        self,
        running: _RunningChannel,
        task_slot: str,
        *,
        timeout: float,
    ) -> bool:
        """Cancel, drain and clear one owned task slot only after settlement."""
        task = getattr(running, task_slot)
        if task is None or task is asyncio.current_task():
            return True
        if not await self._cancel_and_drain_task(task, timeout=timeout):
            return False
        setattr(running, task_slot, None)
        return True

    def _enter_quiescing(self, binding_id: str, running: _RunningChannel) -> None:
        """Apply the shared serving-to-fence-owner state transition."""
        running.quiescing = True
        if self._channel_registry is not None:
            self._channel_registry.unregister_dynamic_channel(running.channel.name)
        logger.debug("Published Feishu runtime entered quiescing ownership", extra={"binding_id": binding_id})

    def _ensure_cleanup_retry(self, binding_id: str, running: _RunningChannel) -> None:
        """Keep exactly one cleanup retry task for a retained generation."""
        cleanup_task = running.cleanup_task
        if cleanup_task is None or cleanup_task.done():
            running.cleanup_task = asyncio.create_task(
                self._retry_local_runtime_stop(
                    binding_id,
                    lease_token=running.lease_token,
                    generation=running.generation,
                )
            )

    async def _stop_channel_with_deadline(
        self,
        running: _RunningChannel,
        *,
        timeout: float,
    ) -> bool:
        """Confirm transport stop by a deadline while retaining a pending task for retry."""
        stop_task = running.stop_task
        if stop_task is None:
            stop_task = asyncio.create_task(running.channel.stop())
            running.stop_task = stop_task
        if not stop_task.done():
            done, _pending = await asyncio.wait({stop_task}, timeout=max(0.0, timeout))
            if stop_task not in done:
                stop_task.cancel()
                return False
        outcome = (await asyncio.gather(stop_task, return_exceptions=True))[0]
        running.stop_task = None
        if isinstance(outcome, asyncio.CancelledError):
            return False
        if isinstance(outcome, BaseException):
            raise outcome
        return True

    async def _release_runtime_with_deadline(
        self,
        row: dict[str, Any],
        running: _RunningChannel,
        *,
        timeout: float,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Persist release by a deadline and retain an unsettled write for cleanup retry."""
        release_task = running.release_task
        if release_task is None:
            release_task = asyncio.create_task(
                self._release_runtime_claim(
                    row,
                    running.lease_token,
                    expected_runtime_generation=running.runtime_generation,
                )
            )
            running.release_task = release_task
        if not release_task.done():
            done, _pending = await asyncio.wait({release_task}, timeout=max(0.0, timeout))
            if release_task not in done:
                # The database commit may already have succeeded even though
                # the coroutine has not returned.  Retain the single writer so
                # cleanup can observe its definitive result instead of
                # cancelling it and guessing whether the lease was released.
                return None, False
        outcome = (await asyncio.gather(release_task, return_exceptions=True))[0]
        running.release_task = None
        if isinstance(outcome, asyncio.CancelledError):
            return None, False
        if isinstance(outcome, BaseException):
            logger.error(
                "Failed to release Feishu runtime lease",
                extra={"binding_id": row["id"], "error_class": type(outcome).__name__},
            )
            return None, True
        if isinstance(outcome, dict):
            running.runtime_generation = int(outcome["runtime_generation"])
        return outcome, True

    async def _renew_provisional_runtime_lease(
        self,
        row: dict[str, Any],
        *,
        lease_token: str,
    ) -> bool:
        """Keep a claimed runtime alive until ready/confirm or fail closed."""
        heartbeat_seconds = max(
            0.01,
            min(
                RUNTIME_LEASE_HEARTBEAT_SECONDS,
                RUNTIME_STARTUP_LEASE_TTL_SECONDS / 3,
            ),
        )
        while True:
            await asyncio.sleep(heartbeat_seconds)
            try:
                renewed = await self._repository.renew_runtime(
                    str(row["agent_id"]),
                    str(row["id"]),
                    owner_user_id=str(row["owner_user_id"]),
                    lease_token=lease_token,
                    lease_seconds=RUNTIME_STARTUP_LEASE_TTL_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Failed to renew provisional Feishu runtime lease",
                    extra={"binding_id": row["id"]},
                )
                return False
            if not renewed:
                return False

    async def _discard_unpublished_runtime(
        self,
        row: dict[str, Any],
        *,
        channel: Channel | None,
        lease_token: str,
        lease_claimed: bool,
        registered: bool,
        attempt: _StartupAttempt,
    ) -> None:
        """Unpublish, then retain retry ownership until the transport exits."""
        binding_id = str(row["id"])
        cleanup_deadline = asyncio.get_running_loop().time() + RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS

        def cleanup_timeout() -> float:
            return max(0.0, cleanup_deadline - asyncio.get_running_loop().time())

        current = self._running.get(binding_id)
        if self._channel_registry is not None and (registered or current is not None):
            self._channel_registry.unregister_dynamic_channel(channel.name if channel is not None else f"feishu:{binding_id}")
        if channel is not None and current is not None and current.lease_token == lease_token:
            attempt.capture_running(current)
            self._enter_quiescing(binding_id, current)
            if not await self._drain_runtime_task(
                current,
                "start_task",
                timeout=cleanup_timeout(),
            ):
                await self._replace_lease_monitor_with_cleanup_retry(
                    binding_id,
                    current,
                    cleanup_deadline=cleanup_deadline,
                )
                return
            try:
                stopped = await self._stop_channel_with_deadline(
                    current,
                    timeout=cleanup_timeout(),
                )
            except Exception:
                logger.warning("Failed to stop unpublished Feishu runtime", extra={"binding_id": binding_id})
                await self._replace_lease_monitor_with_cleanup_retry(
                    binding_id,
                    current,
                    cleanup_deadline=cleanup_deadline,
                )
                return
            if not stopped:
                await self._replace_lease_monitor_with_cleanup_retry(
                    binding_id,
                    current,
                    cleanup_deadline=cleanup_deadline,
                )
                return
        if lease_claimed and current is not None and current.lease_token == lease_token:
            released, settled = await self._release_runtime_with_deadline(
                row,
                current,
                timeout=cleanup_timeout(),
            )
            if not settled or released is None:
                await self._replace_lease_monitor_with_cleanup_retry(
                    binding_id,
                    current,
                    cleanup_deadline=cleanup_deadline,
                )
                return
            attempt.capture_row({**row, **released})
        if current is not None and current.lease_token == lease_token:
            await self._remove_confirmed_exited_runtime(binding_id, current)

    async def _replace_lease_monitor_with_cleanup_retry(
        self,
        binding_id: str,
        running: _RunningChannel,
        *,
        cleanup_deadline: float | None = None,
    ) -> None:
        """Retain exactly one owner until stop and durable release both succeed."""
        if cleanup_deadline is None:
            cleanup_deadline = asyncio.get_running_loop().time() + RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS

        def cleanup_timeout() -> float:
            return max(0.0, cleanup_deadline - asyncio.get_running_loop().time())

        self._enter_quiescing(binding_id, running)
        await self._drain_runtime_task(
            running,
            "lease_task",
            timeout=cleanup_timeout(),
        )
        await self._drain_runtime_task(
            running,
            "startup_lease_task",
            timeout=cleanup_timeout(),
        )
        self._ensure_cleanup_retry(binding_id, running)

    async def _remove_confirmed_exited_runtime(self, binding_id: str, running: _RunningChannel) -> None:
        """Forget a generation only after its transport stop returned."""
        for task in (
            running.start_task,
            running.startup_lease_task,
            running.stop_task,
            running.release_task,
            running.lease_task,
            running.cleanup_task,
        ):
            if task is not None and task is not asyncio.current_task():
                await self._cancel_and_drain_task(
                    task,
                    timeout=RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS,
                )
        self._running.pop(binding_id, None)
        if self._channel_registry is not None:
            self._channel_registry.unregister_dynamic_channel(running.channel.name)

    async def _retry_local_runtime_stop(
        self,
        binding_id: str,
        *,
        lease_token: str,
        generation: object,
    ) -> None:
        """Keep a process-local owner until stop and durable acknowledgement finish."""
        while True:
            await asyncio.sleep(RUNTIME_LEASE_HEARTBEAT_SECONDS)
            current = self._running.get(binding_id)
            if current is None or current.generation is not generation or current.lease_token != lease_token:
                return
            async with self._binding_lifecycle(binding_id):
                if not await self._drain_runtime_task(
                    current,
                    "start_task",
                    timeout=RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS,
                ):
                    continue
                try:
                    await self._stop_runtime(
                        binding_id,
                        stop_timeout=RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Failed to stop retained unpublished Feishu runtime", extra={"binding_id": binding_id})
                    continue
            return

    async def _monitor_runtime_lease(
        self,
        binding_id: str,
        *,
        lease_token: str,
        generation: object,
    ) -> None:
        """Heartbeat a runtime lease and stop locally when another replica revokes it."""
        while True:
            await asyncio.sleep(RUNTIME_LEASE_HEARTBEAT_SECONDS)
            current = self._running.get(binding_id)
            if current is None or current.generation is not generation or current.lease_token != lease_token:
                return
            try:
                renewed = await self._repository.renew_runtime(
                    current.agent_id,
                    binding_id,
                    owner_user_id=current.owner_user_id,
                    lease_token=lease_token,
                    lease_seconds=RUNTIME_LEASE_TTL_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Failed to renew Feishu runtime lease; stopping transport fail closed",
                    extra={"binding_id": binding_id},
                )
                renewed = False
            if renewed:
                continue
            async with self._binding_lifecycle(binding_id):
                latest = self._running.get(binding_id)
                if latest is not None and latest.generation is generation and latest.lease_token == lease_token:
                    try:
                        await self._repository.renew_quiescing_runtime(
                            latest.agent_id,
                            binding_id,
                            owner_user_id=latest.owner_user_id,
                            lease_token=lease_token,
                            lease_seconds=RUNTIME_LEASE_TTL_SECONDS,
                        )
                    except Exception:
                        logger.exception("Failed to extend Feishu quiescing fence", extra={"binding_id": binding_id})
                    try:
                        await self._stop_runtime(binding_id)
                    except Exception:
                        logger.exception("Failed to stop Feishu runtime after lease revocation", extra={"binding_id": binding_id})
                        return
            return

    async def _release_runtime_claim(
        self,
        row: dict[str, Any],
        lease_token: str,
        *,
        expected_runtime_generation: int,
    ) -> dict[str, Any] | None:
        try:
            current = await self._repository.get_for_supervisor(
                str(row["id"]),
                supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
            )
            if current is not None and current.get("runtime_lease_token") == lease_token:
                # A remote stop/delete request advances the generation while
                # deliberately retaining the owner's token. Release the exact
                # token at that newly observed generation.
                expected_runtime_generation = int(current["runtime_generation"])
            return await self._repository.release_runtime(
                str(row["agent_id"]),
                str(row["id"]),
                owner_user_id=str(row["owner_user_id"]),
                lease_token=lease_token,
                expected_runtime_generation=expected_runtime_generation,
            )
        except Exception:
            logger.exception("Failed to release Feishu runtime lease", extra={"binding_id": row["id"]})
            return None

    async def start_binding(self, binding_id: str) -> BindingHealth:
        """Activate and start one binding after its connection reports ready."""
        self._ensure_runtime_admission()
        async with self._binding_lifecycle(binding_id):
            self._ensure_runtime_admission()
            row = await self._binding(binding_id)
            self._ensure_not_deleting(row)
            if row["status"] != "active":
                activated = await self._repository.activate(
                    str(row["agent_id"]),
                    binding_id,
                    owner_user_id=str(row["owner_user_id"]),
                )
                if activated is None:
                    raise BindingNotFoundError(binding_id)
                row = await self._binding(binding_id)
            return await self._start_row(row)

    async def _stop_runtime(
        self,
        binding_id: str,
        *,
        stop_timeout: float | None = None,
    ) -> dict[str, Any] | None:
        running = self._running.get(binding_id)
        if running is None:
            return None
        effective_stop_timeout = RUNTIME_STOP_TIMEOUT_SECONDS if stop_timeout is None else stop_timeout
        self._enter_quiescing(binding_id, running)
        if not await self._drain_runtime_task(
            running,
            "lease_task",
            timeout=RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS,
        ):
            await self._replace_lease_monitor_with_cleanup_retry(binding_id, running)
            raise RuntimeError("Feishu runtime lease monitor did not stop")
        if not await self._drain_runtime_task(
            running,
            "start_task",
            timeout=RUNTIME_STARTUP_TEARDOWN_TIMEOUT_SECONDS,
        ):
            await self._replace_lease_monitor_with_cleanup_retry(binding_id, running)
            raise RuntimeError("Feishu channel start task did not stop")
        try:
            stopped = await self._stop_channel_with_deadline(
                running,
                timeout=effective_stop_timeout,
            )
        except Exception:
            logger.error(
                "Published Feishu binding failed to stop cleanly",
                extra={"binding_id": binding_id},
            )
            await self._replace_lease_monitor_with_cleanup_retry(binding_id, running)
            raise
        if not stopped:
            logger.warning("Published Feishu binding stop timed out", extra={"binding_id": binding_id})
            await self._replace_lease_monitor_with_cleanup_retry(binding_id, running)
            raise RuntimeError("Feishu channel stop timed out")
        released, settled = await self._release_runtime_with_deadline(
            {
                "id": binding_id,
                "agent_id": running.agent_id,
                "owner_user_id": running.owner_user_id,
            },
            running,
            timeout=effective_stop_timeout,
        )
        if not settled or released is None:
            await self._replace_lease_monitor_with_cleanup_retry(binding_id, running)
            raise RuntimeError("Feishu runtime exit acknowledgement could not be persisted")
        await self._remove_confirmed_exited_runtime(binding_id, running)
        return released

    async def _await_runtime_quiesced(self, row: dict[str, Any]) -> None:
        """Wait only for the transport owner to acknowledge its actual exit."""
        binding_id = str(row["id"])
        deadline = asyncio.get_running_loop().time() + RUNTIME_LEASE_RELEASE_WAIT_SECONDS
        while True:
            current = await self._repository.get_for_supervisor(
                binding_id,
                supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
            )
            if current is None or current.get("runtime_lease_token") is None:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise BindingCleanupPendingError(binding_id)
            await asyncio.sleep(0.05)

    async def _handle_runtime_error(
        self,
        binding_id: str,
        detail: str,
        *,
        generation: object,
    ) -> None:
        """Remove one failed runtime and persist unhealthy without touching peers."""
        async with self._binding_lifecycle(binding_id):
            current = self._running.get(binding_id)
            if current is None or current.generation is not generation:
                logger.info(
                    "Ignoring stale Feishu runtime error",
                    extra={"binding_id": binding_id},
                )
                return
            row = await self._binding(binding_id)
            projection_row = row
            try:
                released = await self._stop_runtime(binding_id)
                if released is not None:
                    projection_row = {**row, **released}
            except Exception:
                logger.exception(
                    "Failed to confirm Feishu transport exit after runtime error",
                    extra={"binding_id": binding_id},
                )
            await self._record_health(
                projection_row,
                health="unhealthy",
                detail=detail,
                running=False,
            )

    async def _handle_runtime_health(
        self,
        binding_id: str,
        healthy: bool,
        detail: str | None,
        *,
        generation: object,
    ) -> None:
        """Persist a redacted cleanup-health transition without stopping I/O."""
        async with self._binding_lifecycle(binding_id):
            current = self._running.get(binding_id)
            if current is None or current.generation is not generation:
                logger.info(
                    "Ignoring stale Feishu runtime health update",
                    extra={"binding_id": binding_id},
                )
                return
            row = await self._binding(binding_id)
            try:
                await self._record_health(
                    row,
                    health="healthy" if healthy else "unhealthy",
                    detail=None if healthy else (detail or "Attachment cleanup recovery is pending"),
                    running=self._is_serving(current),
                )
            except _StaleHealthProjectionError:
                logger.info(
                    "Ignoring superseded Feishu runtime health observation",
                    extra={"binding_id": binding_id},
                )

    async def stop_binding(self, binding_id: str) -> BindingHealth:
        """Stop one confirmed runtime before persisting its inactive state."""
        async with self._binding_lifecycle(binding_id):
            row = await self._binding(binding_id)
            requested = await self._repository.request_runtime_stop(
                str(row["agent_id"]),
                binding_id,
                owner_user_id=str(row["owner_user_id"]),
                lease_seconds=RUNTIME_LEASE_TTL_SECONDS,
            )
            if requested is None:
                raise BindingNotFoundError(binding_id)
            running = self._running.get(binding_id)
            if running is not None and running.lease_token == requested.get("runtime_lease_token"):
                running.runtime_generation = int(requested["runtime_generation"])
            try:
                await self._stop_runtime(binding_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise BindingCleanupPendingError(binding_id) from exc
            await self._await_runtime_quiesced({**row, **requested})
            stopped = await self._repository.deactivate(
                str(row["agent_id"]),
                binding_id,
                owner_user_id=str(row["owner_user_id"]),
            )
            if stopped is None:
                raise BindingNotFoundError(binding_id)
            value = BindingHealth(binding_id, str(row["agent_id"]), "unknown", None, False)
            self._health[binding_id] = value
            return value

    async def restart_binding(self, binding_id: str) -> BindingHealth:
        """Replace one binding only after its previous runtime fully exits."""
        self._ensure_runtime_admission()
        async with self._binding_lifecycle(binding_id):
            self._ensure_runtime_admission()
            row = await self._binding(binding_id)
            self._ensure_not_deleting(row)
            if row["status"] != "active":
                activated = await self._repository.activate(
                    str(row["agent_id"]),
                    binding_id,
                    owner_user_id=str(row["owner_user_id"]),
                )
                if activated is None:
                    raise BindingNotFoundError(binding_id)
            await self._stop_runtime(binding_id)
            return await self._start_row(await self._binding(binding_id), strict=True)

    async def rotate_binding_credentials(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
        app_id: str,
        secret_ref: str,
    ) -> dict[str, Any]:
        """Persist and apply credentials under the binding lifecycle lock."""
        self._ensure_runtime_admission()
        async with self._binding_lifecycle(binding_id):
            self._ensure_runtime_admission()
            previous = await self._binding(binding_id)
            self._ensure_not_deleting(previous)
            if str(previous["agent_id"]) != agent_id or str(previous["owner_user_id"]) != owner_user_id:
                raise BindingNotFoundError(binding_id)
            staged = await self._repository.stage_secret_cleanup_from_ingest(
                agent_id,
                binding_id,
                owner_user_id=owner_user_id,
                secret_ref=secret_ref,
            )
            if staged is None:
                raise BindingNotFoundError(binding_id)
            try:
                updated = await self._repository.update_credentials(
                    agent_id,
                    binding_id,
                    owner_user_id=owner_user_id,
                    app_id=app_id,
                    secret_ref=secret_ref,
                )
                if updated is None:
                    raise BindingNotFoundError(binding_id)
                if previous["status"] == "active":
                    await self._stop_runtime(binding_id)
                    await self._start_row(await self._binding(binding_id), strict=True)
            except BaseException:
                rolled_back = await self._repository.update_credentials(
                    agent_id,
                    binding_id,
                    owner_user_id=owner_user_id,
                    app_id=str(previous["app_id"]),
                    secret_ref=str(previous["secret_ref"]),
                )
                if rolled_back is not None and previous["status"] == "active":
                    try:
                        await self._stop_runtime(binding_id)
                        await self._start_row(await self._binding(binding_id), strict=True)
                    except BaseException as rollback_error:
                        logger.error(
                            "Failed to restore Feishu runtime after credential rotation rollback",
                            extra={
                                "binding_id": binding_id,
                                "error_class": type(rollback_error).__name__,
                            },
                        )
                await self._repository.replace_secret_cleanup(
                    agent_id,
                    binding_id,
                    owner_user_id=owner_user_id,
                    expected_ref=secret_ref,
                    secret_ref=secret_ref,
                    reason="rotation_rollback",
                )
                raise
            transitioned = await self._repository.replace_secret_cleanup(
                agent_id,
                binding_id,
                owner_user_id=owner_user_id,
                expected_ref=secret_ref,
                secret_ref=str(previous["secret_ref"]),
                reason="rotation_superseded",
            )
            if transitioned is None:
                raise RuntimeError("Feishu credential cleanup outbox lost rotation ownership")
            return previous

    async def _erase_secret_cleanup_row(self, row: dict[str, Any]) -> bool:
        secret_ref = row.get("secret_cleanup_ref")
        if not isinstance(secret_ref, str) or not secret_ref:
            return False
        if secret_ref == row.get("secret_ref") and row.get("status") != "deleting":
            raise RuntimeError("Refusing to erase the credential currently referenced by an active binding")
        await self._secret_store.delete(secret_ref)
        cleared = await self._repository.clear_secret_cleanup(
            str(row["agent_id"]),
            str(row["id"]),
            owner_user_id=str(row["owner_user_id"]),
            secret_ref=secret_ref,
        )
        if not cleared:
            current = await self._repository.get_for_supervisor(
                str(row["id"]),
                supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
            )
            if current is not None and current.get("secret_cleanup_ref") == secret_ref:
                raise RuntimeError("Feishu credential cleanup outbox acknowledgement failed")
        return True

    async def cleanup_binding_secrets(self, binding_id: str) -> bool:
        """Retry one binding's durable encrypted-secret cleanup outbox."""
        async with self._binding_lifecycle(binding_id):
            row = await self._binding(binding_id)
            if row.get("secret_cleanup_reason") == "rotation_candidate":
                recovered = await self._repository.recover_staged_secret_cleanup(
                    str(row["agent_id"]),
                    binding_id,
                    owner_user_id=str(row["owner_user_id"]),
                )
                if recovered is not None:
                    row = {**row, **recovered}
            return await self._erase_secret_cleanup_row(row)

    async def _recover_due_secret_cleanups(self) -> None:
        rows = await self._repository.list_secret_cleanup_due(
            supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
        )

        async def recover(row: dict[str, Any]) -> None:
            binding_id = str(row["id"])
            async with self._binding_lifecycle(binding_id):
                try:
                    current = await self._binding(binding_id)
                    if current.get("secret_cleanup_reason") == "rotation_candidate":
                        recovered = await self._repository.recover_staged_secret_cleanup(
                            str(current["agent_id"]),
                            binding_id,
                            owner_user_id=str(current["owner_user_id"]),
                        )
                        if recovered is not None:
                            current = {**current, **recovered}
                    await self._erase_secret_cleanup_row(current)
                except BindingNotFoundError:
                    return
                except Exception:
                    logger.exception("Published Feishu credential cleanup recovery failed", extra={"binding_id": binding_id})

        await asyncio.gather(*(recover(row) for row in rows))

    async def _recover_pending_secret_ingests(self) -> None:
        """CAS and erase DB-owned ciphertext whose binding transfer was interrupted."""
        try:
            rows = await self._repository.list_secret_ingests_due(
                supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
            )
        except Exception:
            logger.exception("Failed to list database-owned Feishu credential ingests")
            rows = []
        for row in rows:
            secret_ref = str(row["secret_ref"])
            claim_token = uuid4().hex
            try:
                claimed = await self._repository.claim_secret_ingest_cleanup(
                    secret_ref,
                    claim_token=claim_token,
                    supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
                )
                if claimed is None:
                    continue
                deleted = await self._secret_store.delete(secret_ref)
                if not deleted and claimed.get("writer_token"):
                    # An expired writer can still resume after a process pause
                    # and atomically publish ciphertext. Keep the DB cleanup
                    # claim so that later retries remain its durable owner.
                    continue
                completed = await self._repository.complete_secret_ingest_cleanup(
                    secret_ref,
                    claim_token=claim_token,
                    supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
                )
                if not completed:
                    raise RuntimeError("Feishu credential ingest cleanup acknowledgement failed")
            except Exception:
                logger.exception(
                    "Failed to recover database-owned Feishu credential ingest",
                    extra={"binding_id": row["binding_id"]},
                )

        # Compatibility recovery for pending files written by the previous
        # pre-database protocol. New POST/PATCH paths never create these.
        try:
            records = await self._secret_store.list_pending()
        except Exception:
            logger.exception("Failed to list pending Feishu credential ingests")
            return
        now = time.time()
        for record in records:
            if record.not_before > now:
                continue
            try:
                row = await self._repository.get_for_supervisor(
                    record.binding_id,
                    supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
                )
                owned = (
                    row is not None
                    and str(row["agent_id"]) == record.agent_id
                    and str(row["owner_user_id"]) == record.owner_user_id
                    and record.secret_ref
                    in {
                        row.get("secret_ref"),
                        row.get("secret_cleanup_ref"),
                        row.get("rotation_previous_secret_ref"),
                    }
                )
                if owned:
                    await self._secret_store.acknowledge_pending(record.secret_ref)
                else:
                    # Rolling-upgrade writers do not participate in the DB CAS
                    # protocol. Retain their marker instead of racing a late
                    # binding transfer; operators may drain it after old
                    # gateways have stopped.
                    logger.warning(
                        "Retaining legacy pending Feishu credential during rolling-upgrade safety window",
                        extra={"binding_id": record.binding_id},
                    )
            except Exception:
                logger.exception(
                    "Failed to recover pending Feishu credential ingest",
                    extra={"binding_id": record.binding_id},
                )

    async def delete_binding(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
    ) -> dict[str, Any]:
        """Converge a binding tombstone, runtime and secret to complete deletion."""
        from app.channels.feishu import (
            FEISHU_ATTACHMENT_BACKLOG_SCAN_TIMEOUT_SECONDS,
            has_published_attachment_cleanup_backlog,
        )

        async with self._binding_lifecycle(binding_id) as lock_entry:
            row = await self._binding(binding_id)
            if str(row["agent_id"]) != agent_id or str(row["owner_user_id"]) != owner_user_id:
                raise BindingNotFoundError(binding_id)
            already_deleting = row["status"] == "deleting"
            try:
                attachment_backlog = await asyncio.wait_for(
                    asyncio.to_thread(has_published_attachment_cleanup_backlog, binding_id),
                    timeout=FEISHU_ATTACHMENT_BACKLOG_SCAN_TIMEOUT_SECONDS + 0.5,
                )
            except TimeoutError:
                attachment_backlog = True
            if attachment_backlog:
                raise BindingCleanupPendingError(binding_id)
            was_running = binding_id in self._running
            if not already_deleting:
                marked = await self._repository.mark_deleting(
                    agent_id,
                    binding_id,
                    owner_user_id=owner_user_id,
                    lease_seconds=RUNTIME_LEASE_TTL_SECONDS,
                )
                if marked is None:
                    raise BindingNotFoundError(binding_id)
                row = {**row, **marked}
            running = self._running.get(binding_id)
            if running is not None and running.lease_token == row.get("runtime_lease_token"):
                running.runtime_generation = int(row["runtime_generation"])
            try:
                await self._stop_runtime(binding_id)
            except asyncio.CancelledError:
                raise
            except Exception as stop_error:
                logger.warning(
                    "Published Feishu deletion is waiting for confirmed transport exit",
                    extra={"binding_id": binding_id, "error_class": type(stop_error).__name__},
                )
                raise BindingCleanupPendingError(binding_id) from stop_error
            await self._await_runtime_quiesced(row)
            self._health.pop(binding_id, None)
            if not already_deleting and await asyncio.to_thread(has_published_attachment_cleanup_backlog, binding_id):
                # A final in-flight producer may have persisted work while the
                # runtime was stopping. Restore desired-active recovery before
                # returning a conflict so the retained row remains recoverable.
                restored = await self._repository.restore_deleting(
                    agent_id,
                    binding_id,
                    owner_user_id=owner_user_id,
                )
                if was_running and restored is not None and restored["status"] == "active":
                    await self._start_row({**row, **restored}, strict=True)
                raise BindingCleanupPendingError(binding_id)
            current = await self._binding(binding_id)
            await self._erase_secret_cleanup_row(current)
            await self._secret_store.delete(str(row["secret_ref"]))
            deleted = await self._repository.delete(
                agent_id,
                binding_id,
                owner_user_id=owner_user_id,
            )
            if deleted is None:
                current = await self._repository.get_for_supervisor(
                    binding_id,
                    supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
                )
                if current is not None:
                    raise RuntimeError("Feishu binding tombstone could not be deleted")
                deleted = row
            self._health.pop(binding_id, None)
            lock_entry.retired = True
            return deleted

    async def load_active_bindings(self) -> None:
        """Start all desired-active bindings while isolating per-binding failures."""
        self._ensure_runtime_admission()
        if not self._runtime_leader_owned:
            if self._running:
                raise BindingStartError("Feishu runtime leader must be acquired before local starts")
            if not await self._runtime_leader_fence.acquire():
                raise BindingStartError("Published Feishu runtime leader is already active")
            self._runtime_leader_owned = True
            try:
                recovered = await self._repository.recover_orphaned_runtime_leases(
                    supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
                )
            except BaseException:
                await self._runtime_leader_fence.release()
                self._runtime_leader_owned = False
                raise
            if recovered:
                logger.warning("Recovered %d crash-orphaned Feishu runtime lease(s)", recovered)
        await self._recover_pending_secret_ingests()
        await self._recover_due_secret_cleanups()
        await self._resume_deleting_bindings()
        await self._resume_runtime_stops()
        rows = await self._repository.list_active(
            supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
        )

        async def start_row(row: dict[str, Any]) -> None:
            binding_id = str(row["id"])
            async with self._binding_lifecycle(binding_id):
                await self._converge_startup(
                    row,
                    policy=_STARTUP_RELOAD_POLICY,
                )

        await asyncio.gather(*(start_row(row) for row in rows))
        self._ensure_cleanup_janitor()

    async def _resume_deleting_bindings(self) -> None:
        """Retry every durable tombstone after each cleanup convergence pass."""
        deleting_rows = await self._repository.list_deleting(
            supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
        )

        async def resume_delete(row: dict[str, Any]) -> None:
            binding_id = str(row["id"])
            try:
                await self.delete_binding(
                    str(row["agent_id"]),
                    binding_id,
                    owner_user_id=str(row["owner_user_id"]),
                )
            except BindingCleanupPendingError:
                logger.warning("Published Feishu binding deletion still has attachment cleanup", extra={"binding_id": binding_id})
            except Exception:
                logger.exception("Published Feishu binding deletion recovery failed", extra={"binding_id": binding_id})

        await asyncio.gather(*(resume_delete(row) for row in deleting_rows))

    async def _resume_runtime_stops(self) -> None:
        """Converge durable remote STOP requests after owner release or crash expiry."""
        rows = await self._repository.list_runtime_stop_requested(
            supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
        )

        async def resume_stop(row: dict[str, Any]) -> None:
            binding_id = str(row["id"])
            try:
                await self.stop_binding(binding_id)
            except BindingCleanupPendingError:
                logger.warning(
                    "Published Feishu runtime stop still awaits transport acknowledgement",
                    extra={"binding_id": binding_id},
                )
            except Exception:
                logger.exception(
                    "Published Feishu runtime stop recovery failed",
                    extra={"binding_id": binding_id},
                )

        await asyncio.gather(*(resume_stop(row) for row in rows))

    def _ensure_cleanup_janitor(self) -> None:
        if self._cleanup_janitor_task is not None and not self._cleanup_janitor_task.done():
            return
        self._cleanup_janitor_task = asyncio.create_task(self._run_cleanup_janitor())

    async def _run_cleanup_janitor(self) -> None:
        """Recover attachment jobs independently of binding rows and secrets."""
        from app.channels.feishu import (
            FEISHU_ATTACHMENT_CLEANUP_RETRY_INTERVAL_SECONDS,
        )

        while True:
            try:
                await self.recover_cleanup_state()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Published attachment cleanup janitor pass failed")
            await asyncio.sleep(FEISHU_ATTACHMENT_CLEANUP_RETRY_INTERVAL_SECONDS)

    async def recover_cleanup_state(self) -> int:
        """Run one global attachment/secret/tombstone convergence pass."""
        from app.channels.feishu import recover_all_published_attachment_cleanups

        completed = await recover_all_published_attachment_cleanups()
        await self._recover_pending_secret_ingests()
        await self._recover_due_secret_cleanups()
        await self._resume_runtime_stops()
        await self._resume_deleting_bindings()
        return completed

    async def test_binding(self, binding_id: str) -> BindingHealth:
        """Test credentials and persist only a redacted health result."""
        row = await self._binding(binding_id)
        try:
            credentials = decode_feishu_credentials(await self._secret_store.get(str(row["secret_ref"])))
            healthy, detail = await self._connection_tester(str(row["app_id"]), credentials.app_secret)
            safe_detail = detail.replace(credentials.app_secret, "[REDACTED]") if detail else None
            return await self._record_health(
                row,
                health="healthy" if healthy else "unhealthy",
                detail=safe_detail,
                running=self._is_serving(self._running.get(binding_id)),
            )
        except asyncio.CancelledError:
            raise
        except _StaleHealthProjectionError:
            return await self._current_binding_health(binding_id)
        except Exception as exc:
            logger.error(
                "Published Feishu credential test failed",
                extra={"binding_id": binding_id, "error_class": type(exc).__name__},
            )
            try:
                return await self._record_health(
                    row,
                    health="unhealthy",
                    detail="Feishu credential test failed",
                    running=self._is_serving(self._running.get(binding_id)),
                )
            except _StaleHealthProjectionError:
                return await self._current_binding_health(binding_id)

    async def _current_binding_health(self, binding_id: str) -> BindingHealth:
        """Return the current durable health after an observation loses its epoch."""
        current = await self._binding(binding_id)
        return BindingHealth(
            binding_id=binding_id,
            agent_id=str(current["agent_id"]),
            health=str(current["health"]),
            detail=current.get("health_detail"),
            running=self._is_serving(self._running.get(binding_id)),
        )

    async def shutdown(self) -> None:
        """Stop process-local instances while preserving desired DB status."""
        async with self._shutdown_lock:
            if self._shutdown_complete:
                return
            self._shutting_down = True
            shutdown_deadline = asyncio.get_running_loop().time() + RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS
            try:
                async with asyncio.timeout_at(shutdown_deadline):
                    janitor_task = self._cleanup_janitor_task
                    self._cleanup_janitor_task = None
                    if janitor_task is not None:
                        janitor_task.cancel()
                        await asyncio.gather(janitor_task, return_exceptions=True)

                    async def stop_one(binding_id: str) -> None:
                        async with self._binding_lifecycle(binding_id):
                            try:
                                await self._stop_runtime(binding_id)
                            except Exception:
                                logger.exception("Failed to stop Feishu binding during shutdown", extra={"binding_id": binding_id})

                    lifecycle_ids = set(self._binding_locks) | set(self._running)
                    await asyncio.gather(*(stop_one(binding_id) for binding_id in lifecycle_ids))
                    await self._drain_late_runtime_claim_ownership(deadline=shutdown_deadline)
                    await self._drain_quiescing_runtime_ownership(deadline=shutdown_deadline)
            except TimeoutError as exc:
                logger.error(
                    "Feishu supervisor shutdown timed out with unresolved runtime ownership",
                    extra={
                        "late_claims": len(self._late_runtime_claim_tasks),
                        "late_releases": len(self._late_runtime_release_tasks),
                        "runtime_owners": len(self._running),
                    },
                )
                raise RuntimeError("Feishu supervisor shutdown timed out while draining late runtime claim or quiescing runtime ownership") from exc
            for binding_id, entry in list(self._binding_locks.items()):
                entry.retired = True
                if entry.users == 0 and self._binding_locks.get(binding_id) is entry:
                    self._binding_locks.pop(binding_id, None)
            if self._runtime_leader_owned and not self._running:
                await self._runtime_leader_fence.release()
                self._runtime_leader_owned = False
            self._shutdown_complete = True


__all__ = [
    "BindingCleanupPendingError",
    "BindingHealth",
    "BindingNotFoundError",
    "BindingStartError",
    "FeishuSupervisor",
    "SupervisorShuttingDownError",
]
