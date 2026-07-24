"""Tests for AioSandboxProvider mount helpers."""

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from deerflow.config.paths import Paths, join_host_path
from deerflow.runtime.user_context import reset_current_user, set_current_user

# ── ensure_thread_dirs ───────────────────────────────────────────────────────


def test_ensure_thread_dirs_creates_acp_workspace(tmp_path):
    """ACP workspace directory must be created alongside user-data dirs."""
    paths = Paths(base_dir=tmp_path)
    paths.ensure_thread_dirs("thread-1")

    assert (tmp_path / "threads" / "thread-1" / "user-data" / "workspace").exists()
    assert (tmp_path / "threads" / "thread-1" / "user-data" / "uploads").exists()
    assert (tmp_path / "threads" / "thread-1" / "user-data" / "outputs").exists()
    assert (tmp_path / "threads" / "thread-1" / "acp-workspace").exists()


def test_ensure_thread_dirs_acp_workspace_is_world_writable(tmp_path):
    """ACP workspace must be chmod 0o777 so the ACP subprocess can write into it."""
    paths = Paths(base_dir=tmp_path)
    paths.ensure_thread_dirs("thread-2")

    acp_dir = tmp_path / "threads" / "thread-2" / "acp-workspace"
    mode = oct(acp_dir.stat().st_mode & 0o777)
    assert mode == oct(0o777)


def test_host_thread_dir_rejects_invalid_thread_id(tmp_path):
    paths = Paths(base_dir=tmp_path)

    with pytest.raises(ValueError, match="Invalid thread_id"):
        paths.host_thread_dir("../escape")


# ── _get_thread_mounts ───────────────────────────────────────────────────────


def _make_provider(tmp_path):
    """Build a minimal AioSandboxProvider instance without starting the idle checker."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    with patch.object(aio_mod.AioSandboxProvider, "_start_idle_checker"):
        provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
        provider._config = {}
        provider._sandboxes = {}
        provider._lock = MagicMock()
        provider._idle_checker_stop = MagicMock()
    return provider


def _make_lifecycle_provider(aio_mod: Any, backend: Any) -> Any:
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._config = {"replicas": 3}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._thread_owners = {}
    provider._thread_locks = {}
    provider._last_activity = {}
    provider._sandbox_use_versions = {}
    provider._late_create_cleanup_tasks = set()
    provider._backend_create_operations = {}
    provider._instance_id = aio_mod.uuid.uuid4().hex
    provider._sandbox_lifecycle_paths = {}
    provider._warm_pool = {}
    provider._shutdown_called = False
    provider._idle_checker_stop = aio_mod.threading.Event()
    provider._idle_checker_thread = None
    provider._lock = aio_mod.threading.Lock()
    provider._backend = backend
    return provider


def test_periodic_reconcile_never_adopts_or_destroys_another_live_provider_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-live-owner",
        sandbox_url="http://sandbox-live-owner",
    )
    backend = SimpleNamespace(list_running=lambda: [info], destroy=MagicMock())
    owner = _make_lifecycle_provider(aio_mod, backend)
    observer = _make_lifecycle_provider(aio_mod, backend)
    lifecycle_path = tmp_path / "users" / "owner-a" / "threads" / "thread-live-owner" / f".{info.sandbox_id}.lifecycle.json"
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_path.write_text(
        json.dumps(
            {
                "sandbox_id": info.sandbox_id,
                "thread_id": "thread-live-owner",
                "owner_user_id": "owner-a",
                "owner_instance_id": owner._instance_id,
                "operation_token": "active-owner",
                "generation": 1,
                "state": "active",
                "lease_expires_at": aio_mod.time.time() + 60,
            }
        ),
        encoding="utf-8",
    )

    observer._reconcile_orphans()
    observer._cleanup_idle_sandboxes(0.0001)

    assert observer._warm_pool == {}
    backend.destroy.assert_not_called()
    assert lifecycle_path.exists()


def test_explicit_discovery_refuses_live_cross_provider_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    thread_id = "thread-live-discovery-owner"
    sandbox_id = aio_mod.AioSandboxProvider._deterministic_sandbox_id(f"owner-a\0{thread_id}")
    info = aio_mod.SandboxInfo(sandbox_id=sandbox_id, sandbox_url=f"http://{sandbox_id}")
    backend = SimpleNamespace(discover=MagicMock(return_value=info), destroy=MagicMock())
    owner = _make_lifecycle_provider(aio_mod, backend)
    observer = _make_lifecycle_provider(aio_mod, backend)
    lifecycle_path = tmp_path / "users" / "owner-a" / "threads" / thread_id / f".{sandbox_id}.lifecycle.json"
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_path.write_text(
        json.dumps(
            {
                "sandbox_id": sandbox_id,
                "thread_id": thread_id,
                "owner_user_id": "owner-a",
                "owner_instance_id": owner._instance_id,
                "operation_token": "live-owner",
                "generation": 3,
                "state": "active",
                "lease_expires_at": aio_mod.time.time() + 60,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="owned by another live provider"):
        observer._discover_or_create_with_lock(thread_id, sandbox_id, user_id="owner-a")

    state = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    assert state["owner_instance_id"] == owner._instance_id
    assert observer.get(sandbox_id) is None
    backend.destroy.assert_not_called()


def test_explicit_discovery_can_adopt_expired_cross_provider_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    thread_id = "thread-expired-discovery-owner"
    sandbox_id = aio_mod.AioSandboxProvider._deterministic_sandbox_id(f"owner-a\0{thread_id}")
    info = aio_mod.SandboxInfo(sandbox_id=sandbox_id, sandbox_url=f"http://{sandbox_id}")
    backend = SimpleNamespace(discover=MagicMock(return_value=info), destroy=MagicMock())
    observer = _make_lifecycle_provider(aio_mod, backend)
    lifecycle_path = tmp_path / "users" / "owner-a" / "threads" / thread_id / f".{sandbox_id}.lifecycle.json"
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_path.write_text(
        json.dumps(
            {
                "sandbox_id": sandbox_id,
                "thread_id": thread_id,
                "owner_user_id": "owner-a",
                "owner_instance_id": "expired-owner",
                "operation_token": "expired-owner-token",
                "generation": 4,
                "state": "active",
                "lease_expires_at": aio_mod.time.time() - 1,
            }
        ),
        encoding="utf-8",
    )

    assert observer._discover_or_create_with_lock(thread_id, sandbox_id, user_id="owner-a") == sandbox_id
    state = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    assert state["owner_instance_id"] == observer._instance_id
    assert state["generation"] == 5


def test_reconcile_reclaims_sandbox_from_expired_creating_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-dead-creator",
        sandbox_url="http://sandbox-dead-creator",
    )
    lifecycle_path = tmp_path / "users" / "owner-a" / "threads" / "thread-dead-creator" / f".{info.sandbox_id}.lifecycle.json"
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_path.write_text(
        json.dumps(
            {
                "sandbox_id": info.sandbox_id,
                "thread_id": "thread-dead-creator",
                "owner_user_id": "owner-a",
                "owner_instance_id": "dead-provider",
                "operation_token": "dead-create",
                "generation": 1,
                "state": "creating",
                "created_at": aio_mod.time.time() - 120,
                "lease_expires_at": aio_mod.time.time() - 1,
            }
        ),
        encoding="utf-8",
    )
    backend = SimpleNamespace(list_running=lambda: [info], destroy=MagicMock())
    observer = _make_lifecycle_provider(aio_mod, backend)

    observer._reconcile_orphans()

    backend.destroy.assert_called_once_with(info)
    assert not lifecycle_path.exists()
    assert observer._warm_pool == {}


def test_idle_cleanup_cannot_destroy_sandbox_after_explicit_owner_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-transferred",
        sandbox_url="http://sandbox-transferred",
    )
    backend = SimpleNamespace(list_running=lambda: [info], destroy=MagicMock())
    old_owner = _make_lifecycle_provider(aio_mod, backend)
    new_owner = _make_lifecycle_provider(aio_mod, backend)
    lifecycle_path = tmp_path / "users" / "owner-a" / "threads" / "thread-transferred" / f".{info.sandbox_id}.lifecycle.json"
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_path.write_text(
        json.dumps(
            {
                "sandbox_id": info.sandbox_id,
                "thread_id": "thread-transferred",
                "owner_user_id": "owner-a",
                "owner_instance_id": new_owner._instance_id,
                "operation_token": "new-owner",
                "generation": 2,
                "state": "active",
                "lease_expires_at": aio_mod.time.time() + 60,
            }
        ),
        encoding="utf-8",
    )
    old_owner._sandbox_lifecycle_paths[info.sandbox_id] = lifecycle_path
    old_owner._sandboxes[info.sandbox_id] = aio_mod.AioSandbox(
        id=info.sandbox_id,
        base_url=info.sandbox_url,
    )
    old_owner._sandbox_infos[info.sandbox_id] = info
    old_owner._thread_sandboxes["thread-transferred"] = info.sandbox_id
    old_owner._last_activity[info.sandbox_id] = 0

    old_owner._cleanup_idle_sandboxes(0.001)

    backend.destroy.assert_not_called()
    assert old_owner._sandboxes == {}
    assert lifecycle_path.exists()


def test_get_thread_mounts_includes_acp_workspace(tmp_path, monkeypatch):
    """_get_thread_mounts must include /mnt/acp-workspace (read-only) for docker sandbox."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-3")

    container_paths = {m[1]: (m[0], m[2]) for m in mounts}

    assert "/mnt/acp-workspace" in container_paths, "ACP workspace mount is missing"
    expected_host = str(tmp_path / "threads" / "thread-3" / "acp-workspace")
    actual_host, read_only = container_paths["/mnt/acp-workspace"]
    assert actual_host == expected_host
    assert read_only is True, "ACP workspace should be read-only inside the sandbox"


def test_get_thread_mounts_includes_user_data_dirs(tmp_path, monkeypatch):
    """Baseline: user-data mounts must still be present after the ACP workspace change."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-4")
    container_paths = {m[1] for m in mounts}

    assert "/mnt/user-data/workspace" in container_paths
    assert "/mnt/user-data/uploads" in container_paths
    assert "/mnt/user-data/outputs" in container_paths


def test_aio_provider_scopes_same_thread_cache_id_and_mounts_by_explicit_owner(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", lambda *_args, **_kwargs: True)
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._config = {"replicas": 3}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._thread_owners = {}
    provider._thread_locks = {}
    provider._last_activity = {}
    provider._warm_pool = {}
    provider._lock = aio_mod.threading.Lock()

    create_calls: list[tuple[str | None, str, list[tuple[str, str, bool]] | None]] = []

    def create(thread_id, sandbox_id, *, extra_mounts=None):
        create_calls.append((thread_id, sandbox_id, extra_mounts))
        return aio_mod.SandboxInfo(sandbox_id=sandbox_id, sandbox_url=f"http://{sandbox_id}")

    provider._backend = SimpleNamespace(
        create=create,
        destroy=MagicMock(),
        discover=MagicMock(return_value=None),
    )

    owner_a_id = provider.acquire("shared-thread", user_id="owner-a")
    with pytest.raises(PermissionError, match="different owner"):
        provider.acquire("shared-thread", user_id="owner-b")

    assert len(create_calls) == 1
    mounts_by_id = {sandbox_id: {container: host for host, container, _readonly in mounts or []} for _thread, sandbox_id, mounts in create_calls}
    assert "users/owner-a/threads/shared-thread" in mounts_by_id[owner_a_id]["/mnt/user-data/uploads"].replace("\\", "/")
    owner_b_mounts = {
        container: host
        for host, container, _readonly in provider._get_thread_mounts(
            "shared-thread",
            user_id="owner-b",
        )
    }
    assert "users/owner-b/threads/shared-thread" in owner_b_mounts["/mnt/user-data/uploads"].replace("\\", "/")


def test_join_host_path_preserves_windows_drive_letter_style():
    base = r"C:\Users\demo\deer-flow\backend\.deer-flow"

    joined = join_host_path(base, "threads", "thread-9", "user-data", "outputs")

    assert joined == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-9\user-data\outputs"


def test_get_thread_mounts_preserves_windows_host_path_style(tmp_path, monkeypatch):
    """Docker bind mount sources must keep Windows-style paths intact."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setenv("DEER_FLOW_HOST_BASE_DIR", r"C:\Users\demo\deer-flow\backend\.deer-flow")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-10")

    container_paths = {container_path: host_path for host_path, container_path, _ in mounts}

    assert container_paths["/mnt/user-data/workspace"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\workspace"
    assert container_paths["/mnt/user-data/uploads"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\uploads"
    assert container_paths["/mnt/user-data/outputs"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\outputs"
    assert container_paths["/mnt/acp-workspace"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\acp-workspace"


def test_discover_or_create_only_unlocks_when_lock_succeeds(tmp_path, monkeypatch):
    """Unlock should not run if exclusive locking itself fails."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._discover_or_create_with_lock = aio_mod.AioSandboxProvider._discover_or_create_with_lock.__get__(
        provider,
        aio_mod.AioSandboxProvider,
    )

    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(
        aio_mod,
        "_lock_file_exclusive",
        lambda _lock_file: (_ for _ in ()).throw(RuntimeError("lock failed")),
    )

    unlock_calls: list[object] = []
    monkeypatch.setattr(
        aio_mod,
        "_unlock_file",
        lambda lock_file: unlock_calls.append(lock_file),
    )

    with patch.object(provider, "_create_sandbox", return_value="sandbox-id"):
        with pytest.raises(RuntimeError, match="lock failed"):
            provider._discover_or_create_with_lock("thread-5", "sandbox-5")

    assert unlock_calls == []


@pytest.mark.anyio
async def test_acquire_async_uses_async_readiness_polling(tmp_path, monkeypatch):
    """AioSandboxProvider async creation must not use sync readiness polling."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    provider = _make_provider(None)
    provider._config = {"replicas": 3}
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(
        create=MagicMock(return_value=aio_mod.SandboxInfo(sandbox_id="sandbox-async", sandbox_url="http://sandbox")),
        destroy=MagicMock(),
        discover=MagicMock(return_value=None),
    )

    async_readiness_calls: list[tuple[str, int]] = []

    async def fake_wait_for_sandbox_ready_async(sandbox_url: str, timeout: int = 30, poll_interval: float = 1.0) -> bool:
        async_readiness_calls.append((sandbox_url, timeout))
        return True

    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", fake_wait_for_sandbox_ready_async)
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sync readiness should not be used")),
    )

    sandbox_id = await provider._create_sandbox_async("thread-async", "sandbox-async")

    assert sandbox_id == "sandbox-async"
    assert async_readiness_calls == [("http://sandbox", 60)]
    assert provider._backend.destroy.call_count == 0
    assert provider._thread_sandboxes["thread-async"] == "sandbox-async"


@pytest.mark.anyio
async def test_discover_or_create_with_lock_async_offloads_lock_file_open_and_close(tmp_path, monkeypatch):
    """Async lock path must not open or close lock files on the event loop."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._discover_or_create_with_lock_async = aio_mod.AioSandboxProvider._discover_or_create_with_lock_async.__get__(
        provider,
        aio_mod.AioSandboxProvider,
    )
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {"thread-async-lock": "sandbox-async-lock"}
    provider._sandboxes = {"sandbox-async-lock": aio_mod.AioSandbox(id="sandbox-async-lock", base_url="http://sandbox")}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(discover=MagicMock(return_value=None))

    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))

    to_thread_calls: list[object] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(aio_mod.asyncio, "to_thread", fake_to_thread)

    sandbox_id = await provider._discover_or_create_with_lock_async("thread-async-lock", "sandbox-async-lock")

    assert sandbox_id == "sandbox-async-lock"
    assert aio_mod._open_and_lock_file in to_thread_calls
    assert aio_mod._unlock_and_close_file in to_thread_calls


@pytest.mark.anyio
async def test_acquire_thread_lock_async_uses_dedicated_executor(monkeypatch):
    """Per-thread lock waits should not consume the default asyncio.to_thread pool."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    lock = aio_mod.threading.Lock()

    async def fail_to_thread(*_args, **_kwargs):
        raise AssertionError("thread-lock acquisition must not use asyncio.to_thread")

    monkeypatch.setattr(aio_mod.asyncio, "to_thread", fail_to_thread)

    await aio_mod._acquire_thread_lock_async(lock)
    try:
        assert not lock.acquire(blocking=False)
    finally:
        lock.release()


@pytest.mark.anyio
async def test_acquire_async_cancellation_does_not_leak_thread_lock(tmp_path):
    """Cancelled async lock waiters must not leave the per-thread lock held."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()

    thread_id = "thread-cancel-lock"
    thread_lock = provider._get_thread_lock(thread_id)
    thread_lock.acquire()

    task = asyncio.create_task(provider.acquire_async(thread_id))
    await asyncio.sleep(0.05)
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass

    thread_lock.release()
    deadline = asyncio.get_running_loop().time() + 1
    while asyncio.get_running_loop().time() < deadline:
        acquired = thread_lock.acquire(blocking=False)
        if acquired:
            thread_lock.release()
            return
        await asyncio.sleep(0.01)

    pytest.fail("provider thread lock was leaked after cancelling acquire_async")


@pytest.mark.anyio
async def test_acquire_async_cancelled_waiter_does_not_block_successor(tmp_path, monkeypatch):
    """A cancelled waiter must not prevent the next live waiter from acquiring."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()

    async def fake_acquire_internal_async(thread_id: str | None) -> str:
        assert thread_id == "thread-successor-lock"
        await asyncio.sleep(0)
        return "sandbox-successor"

    monkeypatch.setattr(provider, "_acquire_internal_async", fake_acquire_internal_async)

    thread_id = "thread-successor-lock"
    thread_lock = provider._get_thread_lock(thread_id)
    thread_lock.acquire()

    cancelled_waiter = asyncio.create_task(provider.acquire_async(thread_id))
    await asyncio.sleep(0.05)
    cancelled_waiter.cancel()
    try:
        await cancelled_waiter
    except asyncio.CancelledError:
        pass

    live_waiter = asyncio.create_task(provider.acquire_async(thread_id))
    thread_lock.release()

    assert await asyncio.wait_for(live_waiter, timeout=1) == "sandbox-successor"

    deadline = asyncio.get_running_loop().time() + 1
    while asyncio.get_running_loop().time() < deadline:
        acquired = thread_lock.acquire(blocking=False)
        if acquired:
            thread_lock.release()
            return
        await asyncio.sleep(0.01)

    pytest.fail("provider thread lock was not released after successor acquire_async")


@pytest.mark.anyio
async def test_abandoning_reused_acquisition_keeps_active_thread_sandbox(tmp_path):
    """A timed-out waiter must not release a sandbox accepted by its peer."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._thread_locks = {}
    provider._thread_owners = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {"shared-thread": "sandbox-active"}
    provider._last_activity = {"sandbox-active": 1.0}
    provider._sandbox_use_versions = {"sandbox-active": 1}
    provider._sandboxes = {"sandbox-active": object()}
    provider._lock = aio_mod.threading.Lock()

    acquisition = await provider.acquire_with_lease_async(
        "shared-thread",
        user_id="owner-a",
    )
    provider.abandon_acquisition(acquisition)

    assert provider._thread_sandboxes == {"shared-thread": "sandbox-active"}
    assert "sandbox-active" in provider._sandboxes


@pytest.mark.anyio
async def test_cancelled_async_create_destroys_backend_capacity_that_arrives_late(
    tmp_path,
    monkeypatch,
):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    provider = _make_provider(tmp_path)
    provider._config = {"replicas": 3}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._sandbox_use_versions = {}
    provider._lock = aio_mod.threading.Lock()
    create_started = aio_mod.threading.Event()
    release_create = aio_mod.threading.Event()
    destroyed = asyncio.Event()
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-late-create",
        sandbox_url="http://sandbox-late-create",
    )

    def create(_thread_id: str | None, _sandbox_id: str, *, extra_mounts: object | None = None) -> Any:
        assert extra_mounts is None
        create_started.set()
        release_create.wait()
        return info

    loop = asyncio.get_running_loop()

    def destroy(received) -> None:
        assert received == info
        loop.call_soon_threadsafe(destroyed.set)

    provider._backend = SimpleNamespace(create=create, destroy=destroy)
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])

    creation = asyncio.create_task(
        provider._create_sandbox_async(
            "thread-late-create",
            "sandbox-late-create",
            user_id="owner-a",
        )
    )
    try:
        assert await asyncio.to_thread(create_started.wait, 1.0)
        creation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await creation
    finally:
        release_create.set()

    await asyncio.wait_for(destroyed.wait(), timeout=1.0)
    assert provider._sandboxes == {}


@pytest.mark.anyio
async def test_cancelled_old_create_does_not_destroy_successor_adopted_sandbox(
    tmp_path,
    monkeypatch,
):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    provider = _make_provider(tmp_path)
    provider._config = {"replicas": 3}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._thread_locks = {}
    provider._last_activity = {}
    provider._sandbox_use_versions = {}
    provider._backend_create_operations = {}
    provider._lock = aio_mod.threading.Lock()
    create_started = aio_mod.threading.Event()
    release_create = aio_mod.threading.Event()
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-successor-adopted",
        sandbox_url="http://sandbox-successor-adopted",
    )

    def create(_thread_id, _sandbox_id, *, extra_mounts=None):
        create_started.set()
        release_create.wait()
        return info

    provider._backend = SimpleNamespace(
        create=create,
        destroy=MagicMock(),
    )
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])

    old_create = asyncio.create_task(
        provider._create_sandbox_async(
            "thread-successor-adopted",
            info.sandbox_id,
            user_id="owner-a",
        )
    )
    assert await asyncio.to_thread(create_started.wait, 1.0)
    old_create.cancel()
    with pytest.raises(asyncio.CancelledError):
        await old_create

    provider._register_discovered_sandbox("thread-successor-adopted", info)
    provider._mark_sandbox_use_accepted(info.sandbox_id)
    release_create.set()
    await asyncio.wait_for(
        asyncio.gather(*provider._late_create_cleanup_tasks),
        timeout=1.0,
    )

    provider._backend.destroy.assert_not_called()
    assert provider.get(info.sandbox_id) is not None


@pytest.mark.anyio
async def test_cancelled_create_does_not_destroy_sandbox_adopted_by_second_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    create_started = aio_mod.threading.Event()
    release_create = aio_mod.threading.Event()
    published: dict[str, object] = {}
    thread_id = "thread-cross-provider-adoption"
    sandbox_id = aio_mod.AioSandboxProvider._deterministic_sandbox_id(f"owner-a\0{thread_id}")
    info = aio_mod.SandboxInfo(sandbox_id=sandbox_id, sandbox_url=f"http://{sandbox_id}")

    def create(_thread_id: str | None, _sandbox_id: str, *, extra_mounts: object | None = None) -> Any:
        published["info"] = info
        create_started.set()
        release_create.wait()
        return info

    backend = SimpleNamespace(
        create=create,
        discover=lambda requested_id: published.get("info") if requested_id == sandbox_id else None,
        destroy=MagicMock(),
    )
    provider_a = _make_lifecycle_provider(aio_mod, backend)
    provider_b = _make_lifecycle_provider(aio_mod, backend)
    monkeypatch.setattr(provider_a, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(provider_b, "_get_extra_mounts", lambda *_args, **_kwargs: [])

    cancelled: asyncio.Task[str] | None = None
    adoption: asyncio.Task[str] | None = None
    try:
        cancelled = asyncio.create_task(provider_a.acquire_async(thread_id, user_id="owner-a"))
        assert await asyncio.to_thread(create_started.wait, 5.0)
        assert published.get("info") is info
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        adoption = asyncio.create_task(provider_b.acquire_async(thread_id, user_id="owner-a"))
        adopted_id = await asyncio.wait_for(asyncio.shield(adoption), timeout=5.0)
        assert adopted_id == sandbox_id
        assert provider_b.get(sandbox_id) is not None
    finally:
        # Never strand the backend worker if an assertion or watchdog fails.
        release_create.set()
        for task in (cancelled, adoption):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        cleanup_tasks = tuple(provider_a._late_create_cleanup_tasks)
        if cleanup_tasks:
            await asyncio.wait_for(asyncio.gather(*cleanup_tasks), timeout=5.0)

    backend.destroy.assert_not_called()


@pytest.mark.anyio
async def test_async_create_completing_after_shutdown_is_destroyed_not_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    create_started = aio_mod.threading.Event()
    release_create = aio_mod.threading.Event()
    info = aio_mod.SandboxInfo(sandbox_id="sandbox-shutdown-create", sandbox_url="http://sandbox-shutdown-create")

    def create(_thread_id: str | None, _sandbox_id: str, *, extra_mounts: object | None = None) -> Any:
        create_started.set()
        release_create.wait()
        return info

    backend = SimpleNamespace(create=create, destroy=MagicMock())
    provider = _make_lifecycle_provider(aio_mod, backend)
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    creation = asyncio.create_task(
        provider._create_sandbox_async(
            "thread-shutdown-create",
            info.sandbox_id,
            user_id="owner-a",
        )
    )
    assert await asyncio.to_thread(create_started.wait, 1.0)

    provider.shutdown()
    release_create.set()
    with pytest.raises(RuntimeError, match="shut down"):
        await creation

    backend.destroy.assert_called_once_with(info)
    assert provider._sandboxes == {}
    assert provider._thread_sandboxes == {}


@pytest.mark.anyio
async def test_cancelled_late_cleanup_hands_compensation_to_backend_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    create_started = aio_mod.threading.Event()
    release_create = aio_mod.threading.Event()
    destroyed = asyncio.Event()
    info = aio_mod.SandboxInfo(sandbox_id="sandbox-worker-fallback", sandbox_url="http://sandbox-worker-fallback")
    loop = asyncio.get_running_loop()

    def create(_thread_id: str | None, _sandbox_id: str, *, extra_mounts: object | None = None) -> Any:
        create_started.set()
        release_create.wait()
        return info

    def destroy(received: Any) -> None:
        assert received == info
        loop.call_soon_threadsafe(destroyed.set)

    provider = _make_lifecycle_provider(
        aio_mod,
        SimpleNamespace(create=create, destroy=destroy),
    )
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    creation = asyncio.create_task(
        provider._create_sandbox_async(
            "thread-worker-fallback",
            info.sandbox_id,
            user_id="owner-a",
        )
    )
    assert await asyncio.to_thread(create_started.wait, 1.0)
    creation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creation
    cleanup_task = next(iter(provider._late_create_cleanup_tasks))
    cleanup_task.cancel()
    await asyncio.gather(cleanup_task, return_exceptions=True)

    release_create.set()
    await asyncio.wait_for(destroyed.wait(), timeout=1.0)
    for _ in range(100):
        if not provider._backend_create_operations:
            break
        await asyncio.sleep(0.01)
    assert provider._backend_create_operations == {}


@pytest.mark.anyio
async def test_cleanup_cancelled_after_destroy_starts_keeps_single_fenced_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    create_started = aio_mod.threading.Event()
    release_create = aio_mod.threading.Event()
    destroy_started = aio_mod.threading.Event()
    release_destroy = aio_mod.threading.Event()
    destroy_calls: list[object] = []
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-fenced-destroy",
        sandbox_url="http://sandbox-fenced-destroy",
    )

    def create(_thread_id: str | None, _sandbox_id: str, *, extra_mounts: object = None) -> object:
        create_started.set()
        release_create.wait()
        return info

    def destroy(received: object) -> None:
        destroy_calls.append(received)
        destroy_started.set()
        release_destroy.wait()

    provider = _make_lifecycle_provider(aio_mod, SimpleNamespace(create=create, destroy=destroy))
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    creation = asyncio.create_task(
        provider._create_sandbox_async(
            "thread-fenced-destroy",
            info.sandbox_id,
            user_id="owner-a",
        )
    )
    assert await asyncio.to_thread(create_started.wait, 1.0)
    creation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creation
    cleanup_task = next(iter(provider._late_create_cleanup_tasks))
    release_create.set()
    assert await asyncio.to_thread(destroy_started.wait, 1.0)

    cleanup_task.cancel()
    lifecycle_path = tmp_path / "users" / "owner-a" / "threads" / "thread-fenced-destroy" / f".{info.sandbox_id}.lifecycle.json"
    try:
        await asyncio.sleep(0.05)
        assert destroy_calls == [info]
        assert lifecycle_path.exists()
    finally:
        release_destroy.set()
    await asyncio.wait_for(asyncio.gather(cleanup_task, return_exceptions=True), timeout=1.0)
    assert destroy_calls == [info]
    assert not lifecycle_path.exists()


def test_startup_reconciles_durable_cancelled_create_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    thread_id = "thread-crashed-cleanup"
    sandbox_id = "sandbox-crashed-cleanup"
    lifecycle_path = tmp_path / "users" / "owner-a" / "threads" / thread_id / f".{sandbox_id}.lifecycle.json"
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_path.write_text(
        json.dumps(
            {
                "sandbox_id": sandbox_id,
                "thread_id": thread_id,
                "owner_user_id": "owner-a",
                "operation_token": "dead-operation",
                "generation": 1,
                "state": "cleanup_pending",
            }
        ),
        encoding="utf-8",
    )
    info = aio_mod.SandboxInfo(sandbox_id=sandbox_id, sandbox_url=f"http://{sandbox_id}")
    backend = SimpleNamespace(list_running=lambda: [info], destroy=MagicMock())
    provider = _make_lifecycle_provider(aio_mod, backend)

    provider._reconcile_orphans()

    backend.destroy.assert_called_once_with(info)
    assert provider._warm_pool == {}
    assert not lifecycle_path.exists()


@pytest.mark.anyio
async def test_destroy_failure_retains_durable_cleanup_for_next_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    create_started = aio_mod.threading.Event()
    release_create = aio_mod.threading.Event()
    info = aio_mod.SandboxInfo(sandbox_id="sandbox-retry-destroy", sandbox_url="http://sandbox-retry-destroy")

    def create(_thread_id: str | None, _sandbox_id: str, *, extra_mounts: object | None = None) -> Any:
        create_started.set()
        release_create.wait()
        return info

    def fail_destroy(_info: Any) -> None:
        raise RuntimeError("transient destroy failure")

    provider = _make_lifecycle_provider(aio_mod, SimpleNamespace(create=create, destroy=fail_destroy))
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    creation = asyncio.create_task(provider._create_sandbox_async("thread-retry-destroy", info.sandbox_id, user_id="owner-a"))
    assert await asyncio.to_thread(create_started.wait, 1.0)
    creation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creation
    release_create.set()
    await asyncio.wait_for(asyncio.gather(*provider._late_create_cleanup_tasks), timeout=1.0)

    lifecycle_path = tmp_path / "users" / "owner-a" / "threads" / "thread-retry-destroy" / f".{info.sandbox_id}.lifecycle.json"
    assert lifecycle_path.exists()
    assert provider._backend_create_operations

    recovery_backend = SimpleNamespace(list_running=lambda: [info], destroy=MagicMock())
    restarted = _make_lifecycle_provider(aio_mod, recovery_backend)
    restarted._reconcile_orphans()
    recovery_backend.destroy.assert_called_once_with(info)
    assert not lifecycle_path.exists()


def test_startup_keeps_cleanup_intent_until_sandbox_materializes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    lifecycle_path = tmp_path / "users" / "owner-a" / "threads" / "thread-materializing" / ".sandbox-materializing.lifecycle.json"
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_path.write_text(
        json.dumps(
            {
                "sandbox_id": "sandbox-materializing",
                "thread_id": "thread-materializing",
                "owner_user_id": "owner-a",
                "operation_token": "still-creating",
                "generation": 1,
                "state": "cleanup_pending",
                "created_at": aio_mod.time.time(),
                "cleanup_not_before": aio_mod.time.time() - 1,
            }
        ),
        encoding="utf-8",
    )
    other = aio_mod.SandboxInfo(sandbox_id="sandbox-other", sandbox_url="http://sandbox-other")
    materialized = aio_mod.SandboxInfo(sandbox_id="sandbox-materializing", sandbox_url="http://sandbox-materializing")
    running = [other]
    destroy = MagicMock()
    provider = _make_lifecycle_provider(
        aio_mod,
        SimpleNamespace(list_running=lambda: list(running), destroy=destroy),
    )

    provider._reconcile_orphans()

    assert lifecycle_path.exists()
    running.append(materialized)
    provider._reconcile_orphans()
    destroy.assert_called_once_with(materialized)
    assert not lifecycle_path.exists()


@pytest.mark.anyio
async def test_late_create_after_compensation_deadline_is_still_destroyed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "AIO_LATE_CREATE_COMPENSATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    provider = _make_provider(tmp_path)
    provider._config = {"replicas": 3}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._thread_locks = {}
    provider._last_activity = {}
    provider._sandbox_use_versions = {}
    provider._backend_create_operations = {}
    provider._lock = aio_mod.threading.Lock()
    create_started = aio_mod.threading.Event()
    release_create = aio_mod.threading.Event()
    destroyed = asyncio.Event()
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-after-compensation-deadline",
        sandbox_url="http://sandbox-after-compensation-deadline",
    )

    def create(_thread_id, _sandbox_id, *, extra_mounts=None):
        create_started.set()
        release_create.wait()
        return info

    loop = asyncio.get_running_loop()

    def destroy(_info: Any) -> None:
        loop.call_soon_threadsafe(destroyed.set)

    provider._backend = SimpleNamespace(create=create, destroy=destroy)
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])

    old_create = asyncio.create_task(
        provider._create_sandbox_async(
            "thread-after-compensation-deadline",
            info.sandbox_id,
            user_id="owner-a",
        )
    )
    assert await asyncio.to_thread(create_started.wait, 1.0)
    old_create.cancel()
    with pytest.raises(asyncio.CancelledError):
        await old_create
    await asyncio.sleep(0.03)
    release_create.set()

    await asyncio.wait_for(destroyed.wait(), timeout=1.0)
    await asyncio.wait_for(
        asyncio.gather(*provider._late_create_cleanup_tasks),
        timeout=1.0,
    )
    assert provider._backend_create_operations == {}


@pytest.mark.anyio
async def test_cancelled_file_lock_waiter_releases_and_closes_only_after_worker_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    provider = _make_provider(tmp_path)
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {"thread-file-lock-cancel": "sandbox-file-lock-cancel"}
    provider._sandboxes = {
        "sandbox-file-lock-cancel": aio_mod.AioSandbox(
            id="sandbox-file-lock-cancel",
            base_url="http://sandbox",
        )
    }
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(discover=MagicMock(return_value=None))
    lock_worker_entered = aio_mod.threading.Event()
    release_lock_worker = aio_mod.threading.Event()
    first_waiter = True
    lock_errors: list[Exception] = []
    unlock_count = 0

    def blocking_lock(lock_file):
        nonlocal first_waiter
        if first_waiter:
            first_waiter = False
            lock_worker_entered.set()
            release_lock_worker.wait()
        try:
            lock_file.fileno()
        except Exception as exc:  # pragma: no branch - regression observation
            lock_errors.append(exc)

    def unlock(_lock_file):
        nonlocal unlock_count
        unlock_count += 1

    monkeypatch.setattr(aio_mod, "_lock_file_exclusive", blocking_lock)
    monkeypatch.setattr(aio_mod, "_unlock_file", unlock)

    cancelled = asyncio.create_task(
        provider._discover_or_create_with_lock_async(
            "thread-file-lock-cancel",
            "sandbox-file-lock-cancel",
            user_id="owner-a",
        )
    )
    assert await asyncio.to_thread(lock_worker_entered.wait, 1.0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release_lock_worker.set()

    for _ in range(100):
        if unlock_count:
            break
        await asyncio.sleep(0.01)
    assert lock_errors == []
    assert unlock_count == 1
    assert (
        await asyncio.wait_for(
            provider._discover_or_create_with_lock_async(
                "thread-file-lock-cancel",
                "sandbox-file-lock-cancel",
                user_id="owner-a",
            ),
            timeout=1.0,
        )
        == "sandbox-file-lock-cancel"
    )


def test_remote_backend_create_forwards_effective_user_id(monkeypatch):
    """Provisioner mode must receive user_id so PVC subPath matches user isolation."""
    remote_mod = importlib.import_module("deerflow.community.aio_sandbox.remote_backend")
    backend = remote_mod.RemoteSandboxBackend("http://provisioner:8002")
    token = set_current_user(SimpleNamespace(id="user-7"))
    posted: dict = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sandbox_url": "http://sandbox.local"}

    def _post(url, json, timeout):  # noqa: A002 - mirrors requests.post kwarg
        posted.update({"url": url, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(remote_mod.requests, "post", _post)

    try:
        backend.create("thread-42", "sandbox-42")
    finally:
        reset_current_user(token)

    assert posted["url"] == "http://provisioner:8002/api/sandboxes"
    assert posted["json"] == {
        "sandbox_id": "sandbox-42",
        "thread_id": "thread-42",
        "user_id": "user-7",
    }
