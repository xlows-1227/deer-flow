"""Shared synchronization helpers for Published Feishu shutdown tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class ShutdownTestSupervisor(Protocol):
    """Minimal Supervisor surface required by shutdown-test cleanup."""

    _shutdown_complete: bool

    @property
    def owned_binding_ids(self) -> tuple[str, ...]: ...

    async def shutdown(self) -> None: ...


class ShutdownTestFence(Protocol):
    """Read-only leader-fence state used by the cleanup contract."""

    held: bool


class RuntimeTokenRepository(Protocol):
    """Repository surface needed to observe durable runtime-token cleanup."""

    async def get(
        self,
        agent_id: str,
        binding_id: str,
        *,
        owner_user_id: str,
    ) -> dict[str, Any] | None: ...


@dataclass
class CleanupRetryBarrier:
    """Drive one failed stop attempt into an explicitly released retry."""

    delegate: Callable[[], Awaitable[None]]
    attempts: int = 0
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    recovered: asyncio.Event = field(default_factory=asyncio.Event)

    async def stop(self) -> None:
        self.attempts += 1
        if self.attempts > 1:
            self.entered.set()
            await self.release.wait()
        await self.delegate()
        if self.attempts > 1:
            self.recovered.set()


async def wait_for_supervisor_ownership(
    supervisor: ShutdownTestSupervisor,
    *,
    attempts: int = 100,
    interval: float = 0.01,
) -> None:
    """Wait for process-local runtime ownership or fail closed."""
    for _ in range(attempts):
        if supervisor.owned_binding_ids == ():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Supervisor ownership did not converge during test cleanup")


async def wait_for_runtime_token_clear(
    repository: RuntimeTokenRepository,
    binding: dict[str, Any],
    *,
    owner_user_id: str,
    timeout: float = 1.0,
) -> dict[str, Any]:
    """Wait for the durable fencing token to clear before local shutdown retry."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        current = await repository.get(
            str(binding["agent_id"]),
            str(binding["id"]),
            owner_user_id=owner_user_id,
        )
        if current is None:
            raise AssertionError("runtime binding disappeared before token cleanup")
        if current["runtime_lease_token"] is None:
            return current
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("runtime token was not reconciled before the deadline")
        await asyncio.sleep(0.01)


async def finish_supervisor_cleanup(
    supervisor: ShutdownTestSupervisor,
    *,
    fence: ShutdownTestFence | None = None,
    attempts: int = 100,
    interval: float = 0.01,
) -> None:
    """Fail closed unless local ownership converges before shutdown."""
    try:
        await wait_for_supervisor_ownership(
            supervisor,
            attempts=attempts,
            interval=interval,
        )
    except AssertionError:
        if fence is not None and not fence.held:
            raise AssertionError("Leader fence was released before Supervisor ownership converged") from None
        raise
    if not supervisor._shutdown_complete:
        await supervisor.shutdown()


def raise_test_cleanup_errors(
    test_error: BaseException | None,
    cleanup_errors: Sequence[BaseException],
    *,
    message: str,
) -> None:
    """Preserve both a regression failure and every independent cleanup failure."""
    errors = ([test_error] if test_error is not None else []) + list(cleanup_errors)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(message, errors)
