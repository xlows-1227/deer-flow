"""Database-driven lifecycle supervisor for Published-Agent Feishu bindings."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.channels.base import Channel
from app.channels.contracts import EventDeduplicator
from app.channels.message_bus import MessageBus
from deerflow.persistence.agent_channel import SYSTEM_CHANNEL_SUPERVISOR_SCOPE, AgentChannelRepository
from deerflow.publishing.feishu_credentials import decode_feishu_credentials
from deerflow.publishing.secret_store import SecretStore

logger = logging.getLogger(__name__)

ConnectionTester = Callable[[str, str], Awaitable[tuple[bool, str]]]


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
    ) -> Channel: ...


class DynamicChannelRegistry(Protocol):
    """Registry seam exposed by the legacy ChannelService."""

    def register_dynamic_channel(self, channel: Channel) -> None: ...

    def unregister_dynamic_channel(self, channel_name: str) -> None: ...


class BindingNotFoundError(LookupError):
    """Raised when a binding disappears before a lifecycle operation."""


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
    ) -> None:
        """Configure isolated binding lifecycles and trusted system seams."""
        self._repository = repository
        self._secret_store = secret_store
        self._bus = bus
        self._channel_factory = channel_factory or _default_channel_factory
        self._connection_tester = connection_tester or _default_connection_tester
        self._channel_registry = channel_registry
        self._event_deduplicator = event_deduplicator
        self._running: dict[str, _RunningChannel] = {}
        self._health: dict[str, BindingHealth] = {}
        self._lifecycle_lock = asyncio.Lock()

    @property
    def running_binding_ids(self) -> tuple[str, ...]:
        """Return process-local bindings whose ready handshake completed."""
        return tuple(sorted(self._running))

    def health(self) -> dict[str, BindingHealth]:
        """Return a snapshot of redacted process-local binding health."""
        return dict(self._health)

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
    ) -> BindingHealth:
        safe_detail = detail[:512] if detail else None
        await self._repository.update_health(
            str(row["agent_id"]),
            str(row["id"]),
            owner_user_id=str(row["owner_user_id"]),
            health=health,
            detail=safe_detail,
        )
        value = BindingHealth(
            binding_id=str(row["id"]),
            agent_id=str(row["agent_id"]),
            health=health,
            detail=safe_detail,
            running=running,
        )
        self._health[value.binding_id] = value
        return value

    async def _start_row(self, row: dict[str, Any]) -> BindingHealth:
        binding_id = str(row["id"])
        existing = self._running.get(binding_id)
        if existing is not None and existing.channel.is_running:
            return await self._record_health(row, health="healthy", detail=None, running=True)

        channel: Channel | None = None
        generation = object()
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
            }
            if self._event_deduplicator is not None:
                channel_kwargs["event_deduplicator"] = self._event_deduplicator
            channel = self._channel_factory(self._bus, **channel_kwargs)
            await channel.start()
            if not channel.is_running:
                raise RuntimeError("channel did not enter running state")
            self._running[binding_id] = _RunningChannel(
                channel=channel,
                owner_user_id=str(row["owner_user_id"]),
                agent_id=str(row["agent_id"]),
                generation=generation,
            )
            if self._channel_registry is not None:
                self._channel_registry.register_dynamic_channel(channel)
            logger.info(
                "Published Feishu binding started",
                extra={"binding_id": binding_id, "agent_id": row["agent_id"]},
            )
            return await self._record_health(row, health="healthy", detail=None, running=True)
        except asyncio.CancelledError:
            if channel is not None:
                await channel.stop()
            raise
        except Exception as exc:
            if channel is not None:
                try:
                    await channel.stop()
                except Exception:
                    pass
            self._running.pop(binding_id, None)
            if self._channel_registry is not None:
                self._channel_registry.unregister_dynamic_channel(f"feishu:{binding_id}")
            logger.error(
                "Published Feishu binding failed to start",
                extra={
                    "binding_id": binding_id,
                    "agent_id": row["agent_id"],
                    "error_class": type(exc).__name__,
                },
            )
            return await self._record_health(
                row,
                health="unhealthy",
                detail="Feishu channel failed to start",
                running=False,
            )

    async def start_binding(self, binding_id: str) -> BindingHealth:
        """Activate and start one binding after its connection reports ready."""
        async with self._lifecycle_lock:
            row = await self._binding(binding_id)
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

    async def _stop_runtime(self, binding_id: str) -> None:
        running = self._running.get(binding_id)
        if running is None:
            return
        try:
            await asyncio.wait_for(running.channel.stop(), timeout=10.0)
        except TimeoutError as exc:
            logger.warning("Published Feishu binding stop timed out", extra={"binding_id": binding_id})
            raise RuntimeError("Feishu channel stop timed out") from exc
        except Exception as exc:
            logger.error(
                "Published Feishu binding failed to stop cleanly",
                extra={"binding_id": binding_id, "error_class": type(exc).__name__},
            )
            raise
        self._running.pop(binding_id, None)
        if self._channel_registry is not None:
            self._channel_registry.unregister_dynamic_channel(running.channel.name)

    async def _handle_runtime_error(
        self,
        binding_id: str,
        detail: str,
        *,
        generation: object,
    ) -> None:
        """Remove one failed runtime and persist unhealthy without touching peers."""
        async with self._lifecycle_lock:
            current = self._running.get(binding_id)
            if current is None or current.generation is not generation:
                logger.info(
                    "Ignoring stale Feishu runtime error",
                    extra={"binding_id": binding_id},
                )
                return
            row = await self._binding(binding_id)
            running = self._running.pop(binding_id, None)
            if running is not None and self._channel_registry is not None:
                self._channel_registry.unregister_dynamic_channel(running.channel.name)
            await self._record_health(
                row,
                health="unhealthy",
                detail=detail,
                running=False,
            )

    async def stop_binding(self, binding_id: str) -> BindingHealth:
        """Stop one confirmed runtime before persisting its inactive state."""
        async with self._lifecycle_lock:
            row = await self._binding(binding_id)
            await self._stop_runtime(binding_id)
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
        async with self._lifecycle_lock:
            row = await self._binding(binding_id)
            if row["status"] != "active":
                activated = await self._repository.activate(
                    str(row["agent_id"]),
                    binding_id,
                    owner_user_id=str(row["owner_user_id"]),
                )
                if activated is None:
                    raise BindingNotFoundError(binding_id)
            await self._stop_runtime(binding_id)
            return await self._start_row(await self._binding(binding_id))

    async def load_active_bindings(self) -> None:
        """Start all desired-active bindings while isolating per-binding failures."""
        rows = await self._repository.list_active(
            supervisor_scope=SYSTEM_CHANNEL_SUPERVISOR_SCOPE,
        )
        for row in rows:
            async with self._lifecycle_lock:
                await self._start_row(row)

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
                running=binding_id in self._running,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Published Feishu credential test failed",
                extra={"binding_id": binding_id, "error_class": type(exc).__name__},
            )
            return await self._record_health(
                row,
                health="unhealthy",
                detail="Feishu credential test failed",
                running=binding_id in self._running,
            )

    async def shutdown(self) -> None:
        """Stop process-local instances while preserving desired DB status."""
        async with self._lifecycle_lock:
            for binding_id in list(self._running):
                try:
                    await self._stop_runtime(binding_id)
                except Exception:
                    logger.exception("Failed to stop Feishu binding during shutdown", extra={"binding_id": binding_id})


__all__ = [
    "BindingHealth",
    "BindingNotFoundError",
    "FeishuSupervisor",
]
