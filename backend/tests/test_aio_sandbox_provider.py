"""Tests for AioSandboxProvider mount helpers."""

import asyncio
import importlib
from types import SimpleNamespace
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

    def create(_thread_id, _sandbox_id, *, extra_mounts=None):
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
    assert await asyncio.to_thread(create_started.wait, 1.0)
    creation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creation
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
async def test_late_create_after_compensation_deadline_is_still_destroyed(
    tmp_path,
    monkeypatch,
):
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

    def destroy(_info):
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
    tmp_path,
    monkeypatch,
):
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
