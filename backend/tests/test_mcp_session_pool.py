"""Tests for the MCP persistent-session pool."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.mcp.session_pool import MCPSessionPool, get_session_pool, reset_session_pool


@pytest.fixture(autouse=True)
def _reset_pool():
    reset_session_pool()
    yield
    reset_session_pool()


# ---------------------------------------------------------------------------
# MCPSessionPool unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_creates_new():
    """First call for a key creates a new session."""
    pool = MCPSessionPool()

    mock_session = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("langchain_mcp_adapters.sessions.create_session", return_value=mock_cm):
        session = await pool.get_session("server", "thread-1", {"transport": "stdio", "command": "x", "args": []})

    assert session is mock_session
    mock_session.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_session_reuses_existing():
    """Second call for the same key returns the cached session."""
    pool = MCPSessionPool()

    mock_session = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("langchain_mcp_adapters.sessions.create_session", return_value=mock_cm):
        s1 = await pool.get_session("server", "thread-1", {"transport": "stdio", "command": "x", "args": []})
        s2 = await pool.get_session("server", "thread-1", {"transport": "stdio", "command": "x", "args": []})

    assert s1 is s2
    # Only one session should have been created.
    assert mock_cm.__aenter__.await_count == 1


@pytest.mark.asyncio
async def test_different_scope_creates_different_session():
    """Different scope keys get different sessions."""
    pool = MCPSessionPool()

    sessions = [AsyncMock(), AsyncMock()]
    idx = 0

    class CmFactory:
        def __init__(self):
            self.enter_count = 0

        async def __aenter__(self):
            nonlocal idx
            s = sessions[idx]
            idx += 1
            self.enter_count += 1
            return s

        async def __aexit__(self, *args):
            return False

    with patch("langchain_mcp_adapters.sessions.create_session", side_effect=lambda *a, **kw: CmFactory()):
        s1 = await pool.get_session("server", "thread-1", {"transport": "stdio", "command": "x", "args": []})
        s2 = await pool.get_session("server", "thread-2", {"transport": "stdio", "command": "x", "args": []})

    assert s1 is not s2
    assert s1 is sessions[0]
    assert s2 is sessions[1]


@pytest.mark.asyncio
async def test_lru_eviction():
    """Oldest entries are evicted when the pool is full."""
    pool = MCPSessionPool()
    pool.MAX_SESSIONS = 2

    class CmFactory:
        def __init__(self):
            self.closed = False

        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            self.closed = True
            return False

    cms: list[CmFactory] = []

    def make_cm(*a, **kw):
        cm = CmFactory()
        cms.append(cm)
        return cm

    with patch("langchain_mcp_adapters.sessions.create_session", side_effect=make_cm):
        await pool.get_session("s", "t1", {"transport": "stdio", "command": "x", "args": []})
        await pool.get_session("s", "t2", {"transport": "stdio", "command": "x", "args": []})
        # Pool is full (2). Adding t3 should evict t1.
        await pool.get_session("s", "t3", {"transport": "stdio", "command": "x", "args": []})

    assert cms[0].closed is True
    assert cms[1].closed is False
    assert cms[2].closed is False


@pytest.mark.asyncio
async def test_close_scope():
    """close_scope shuts down sessions for a specific scope key."""
    pool = MCPSessionPool()

    class CmFactory:
        def __init__(self):
            self.closed = False

        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            self.closed = True
            return False

    cms: list[CmFactory] = []

    def make_cm(*a, **kw):
        cm = CmFactory()
        cms.append(cm)
        return cm

    with patch("langchain_mcp_adapters.sessions.create_session", side_effect=make_cm):
        await pool.get_session("s", "t1", {"transport": "stdio", "command": "x", "args": []})
        await pool.get_session("s", "t2", {"transport": "stdio", "command": "x", "args": []})

    await pool.close_scope("t1")

    assert cms[0].closed is True
    assert cms[1].closed is False

    # t2 session still exists.
    assert ("s", "t2") in pool._entries


@pytest.mark.asyncio
async def test_close_all():
    """close_all shuts down every session."""
    pool = MCPSessionPool()

    class CmFactory:
        def __init__(self):
            self.closed = False

        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            self.closed = True
            return False

    cms: list[CmFactory] = []

    def make_cm(*a, **kw):
        cm = CmFactory()
        cms.append(cm)
        return cm

    with patch("langchain_mcp_adapters.sessions.create_session", side_effect=make_cm):
        await pool.get_session("s1", "t1", {"transport": "stdio", "command": "x", "args": []})
        await pool.get_session("s2", "t2", {"transport": "stdio", "command": "x", "args": []})

    await pool.close_all()

    assert all(cm.closed for cm in cms)
    assert len(pool._entries) == 0


@pytest.mark.asyncio
async def test_concurrent_get_session_same_key_creates_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Concurrent callers for one scope must share a single session."""
    import asyncio

    pool = MCPSessionPool()
    created: list[Any] = []

    class SlowCM:
        """Yield control inside __aenter__ to widen the creation race."""

        def __init__(self) -> None:
            self.session = AsyncMock()
            self.exited = 0

        async def __aenter__(self):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return self.session

        async def __aexit__(self, *args):
            self.exited += 1
            return False

    def make_cm(*_a: Any, **_kw: Any) -> SlowCM:
        cm = SlowCM()
        created.append(cm)
        return cm

    connection = {"transport": "stdio", "command": "x", "args": []}
    with (
        patch("langchain_mcp_adapters.sessions.create_session", side_effect=make_cm),
        caplog.at_level("INFO"),
    ):
        sessions = await asyncio.gather(
            *[pool.get_session("server", "thread-1", connection) for _ in range(20)]
        )

    assert len(created) == 1
    assert all(session is sessions[0] for session in sessions)
    assert created[0].exited == 0
    created_logs = [r for r in caplog.records if "Created persistent MCP session" in r.getMessage()]
    assert len(created_logs) == 1


@pytest.mark.asyncio
async def test_replaced_session_is_closed_explicitly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A session from another loop is closed, not dropped for the GC."""
    import asyncio

    pool = MCPSessionPool()
    old_loop = asyncio.new_event_loop()
    old_cm = _RecordingCM()
    key = ("server", "thread-1")
    try:
        pool._entries[key] = (old_cm.session, old_loop)
        pool._context_managers[key] = old_cm

        new_cm = _RecordingCM()
        with (
            patch("langchain_mcp_adapters.sessions.create_session", return_value=new_cm),
            caplog.at_level("WARNING"),
        ):
            session = await pool.get_session("server", "thread-1", {"transport": "stdio"})

        assert session is new_cm.session
        assert old_cm.exit_count == 1  # explicit close, not left to GC
        assert old_cm.exit_task is not None
        assert "Replaced existing MCP session" in caplog.text
    finally:
        old_loop.close()


@pytest.mark.asyncio
async def test_replaced_session_close_error_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An anyio cancel-scope error while closing is logged, never escapes."""
    import asyncio

    pool = MCPSessionPool()
    old_loop = asyncio.new_event_loop()
    old_cm = _RecordingCM()
    old_cm.exit_error = RuntimeError(
        "Attempted to exit cancel scope in a different task than it was entered in"
    )
    key = ("server", "thread-1")
    try:
        pool._entries[key] = (old_cm.session, old_loop)
        pool._context_managers[key] = old_cm

        new_cm = _RecordingCM()
        with (
            patch("langchain_mcp_adapters.sessions.create_session", return_value=new_cm),
            caplog.at_level("WARNING"),
        ):
            session = await pool.get_session("server", "thread-1", {"transport": "stdio"})

        assert session is new_cm.session
        assert old_cm.exit_count == 1
        assert "Error closing MCP session" in caplog.text
        assert "Replaced existing MCP session" in caplog.text
    finally:
        old_loop.close()


@pytest.mark.asyncio
async def test_closed_loop_key_locks_are_pruned() -> None:
    """Locks registered for loops closed by the sync wrapper must not leak."""
    import asyncio

    pool = MCPSessionPool()
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()

    pool._get_key_lock(("server", "scope"), closed_loop)
    assert any(entry[2] is closed_loop for entry in pool._key_locks)

    current_loop = asyncio.get_running_loop()
    lock = pool._get_key_lock(("server", "scope"), current_loop)

    assert len(pool._key_locks) == 1
    assert all(not entry[2].is_closed() for entry in pool._key_locks)
    assert pool._get_key_lock(("server", "scope"), current_loop) is lock


@pytest.mark.asyncio
async def test_close_cm_timeout_does_not_block_same_key(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung __aexit__ times out and releases the per-key lock."""
    import asyncio

    monkeypatch.setattr(MCPSessionPool, "SESSION_CLOSE_TIMEOUT", 0.05)
    pool = MCPSessionPool()
    other_loop = asyncio.new_event_loop()
    hanging_cm = _HangingCM()
    key = ("server", "thread-1")
    try:
        pool._entries[key] = (hanging_cm.session, other_loop)
        pool._context_managers[key] = hanging_cm

        new_cm = _RecordingCM()
        with (
            patch("langchain_mcp_adapters.sessions.create_session", return_value=new_cm),
            caplog.at_level("WARNING"),
        ):
            session = await asyncio.wait_for(
                pool.get_session("server", "thread-1", {"transport": "stdio"}),
                timeout=2.0,
            )
            # The same key must be usable again right away (lock released).
            again = await asyncio.wait_for(
                pool.get_session("server", "thread-1", {"transport": "stdio"}),
                timeout=2.0,
            )

        assert session is new_cm.session
        assert again is new_cm.session
        assert hanging_cm.exit_count == 1
        assert "Timed out closing MCP session" in caplog.text
    finally:
        other_loop.close()


class _HangingCM:
    """Fake context manager whose __aexit__ never returns on its own."""

    def __init__(self) -> None:
        self.session = AsyncMock()
        self.exit_count = 0

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        import asyncio

        self.exit_count += 1
        await asyncio.sleep(30)
        return False


class _RecordingCM:
    """Fake MCP context manager recording enter/exit tasks."""

    def __init__(self) -> None:
        self.session = AsyncMock()
        self.enter_task = None
        self.exit_task = None
        self.exit_count = 0
        self.exit_error: Exception | None = None

    async def __aenter__(self):
        import asyncio

        self.enter_task = asyncio.current_task()
        return self.session

    async def __aexit__(self, *args):
        import asyncio

        self.exit_task = asyncio.current_task()
        self.exit_count += 1
        if self.exit_error is not None:
            raise self.exit_error
        return False


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


def test_get_session_pool_singleton():
    """get_session_pool returns the same instance."""
    p1 = get_session_pool()
    p2 = get_session_pool()
    assert p1 is p2


def test_reset_session_pool():
    """reset_session_pool clears the singleton."""
    p1 = get_session_pool()
    reset_session_pool()
    p2 = get_session_pool()
    assert p1 is not p2


# ---------------------------------------------------------------------------
# Integration: _make_session_pool_tool uses the pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_pool_tool_wrapping():
    """The wrapper tool delegates to a pool-managed session."""
    # Build a dummy StructuredTool (as returned by langchain-mcp-adapters).
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    from deerflow.mcp.tools import _make_session_pool_tool

    class Args(BaseModel):
        url: str = Field(..., description="url")

    original_tool = StructuredTool(
        name="playwright_navigate",
        description="Navigate browser",
        args_schema=Args,
        coroutine=AsyncMock(),
        response_format="content_and_artifact",
    )

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=MagicMock(content=[], isError=False, structuredContent=None))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    connection = {"transport": "stdio", "command": "pw", "args": []}

    with patch("langchain_mcp_adapters.sessions.create_session", return_value=mock_cm):
        wrapped = _make_session_pool_tool(original_tool, "playwright", connection)

        # Simulate a tool call with a runtime context containing thread_id.
        mock_runtime = MagicMock()
        mock_runtime.context = {"thread_id": "thread-42"}
        mock_runtime.config = {}

        await wrapped.coroutine(runtime=mock_runtime, url="https://example.com")

    mock_session.call_tool.assert_awaited_once_with("navigate", {"url": "https://example.com"})


@pytest.mark.asyncio
async def test_session_pool_tool_extracts_thread_id():
    """Thread ID is extracted from runtime.config when not in context."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    from deerflow.mcp.tools import _make_session_pool_tool

    class Args(BaseModel):
        x: int = Field(..., description="x")

    original_tool = StructuredTool(
        name="server_tool",
        description="test",
        args_schema=Args,
        coroutine=AsyncMock(),
        response_format="content_and_artifact",
    )

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=MagicMock(content=[], isError=False, structuredContent=None))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("langchain_mcp_adapters.sessions.create_session", return_value=mock_cm):
        wrapped = _make_session_pool_tool(original_tool, "server", {"transport": "stdio", "command": "x", "args": []})

        mock_runtime = MagicMock()
        mock_runtime.context = {}
        mock_runtime.config = {"configurable": {"thread_id": "from-config"}}

        await wrapped.coroutine(runtime=mock_runtime, x=1)

    # Verify the session was created with the correct scope key.
    pool = get_session_pool()
    assert ("server", "from-config") in pool._entries


@pytest.mark.asyncio
async def test_session_pool_tool_default_scope():
    """When no thread_id is available, 'default' is used as scope key."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    from deerflow.mcp.tools import _make_session_pool_tool

    class Args(BaseModel):
        x: int = Field(..., description="x")

    original_tool = StructuredTool(
        name="server_tool",
        description="test",
        args_schema=Args,
        coroutine=AsyncMock(),
        response_format="content_and_artifact",
    )

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=MagicMock(content=[], isError=False, structuredContent=None))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("langchain_mcp_adapters.sessions.create_session", return_value=mock_cm):
        wrapped = _make_session_pool_tool(original_tool, "server", {"transport": "stdio", "command": "x", "args": []})

        # No thread_id in runtime at all.
        await wrapped.coroutine(runtime=None, x=1)

    pool = get_session_pool()
    assert ("server", "default") in pool._entries


@pytest.mark.asyncio
async def test_session_pool_tool_get_config_fallback():
    """When runtime is None, get_config() provides thread_id as fallback."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    from deerflow.mcp.tools import _make_session_pool_tool

    class Args(BaseModel):
        x: int = Field(..., description="x")

    original_tool = StructuredTool(
        name="server_tool",
        description="test",
        args_schema=Args,
        coroutine=AsyncMock(),
        response_format="content_and_artifact",
    )

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=MagicMock(content=[], isError=False, structuredContent=None))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    fake_config = {"configurable": {"thread_id": "from-langgraph-config"}}

    with (
        patch("langchain_mcp_adapters.sessions.create_session", return_value=mock_cm),
        patch("deerflow.mcp.tools.get_config", return_value=fake_config),
    ):
        wrapped = _make_session_pool_tool(original_tool, "server", {"transport": "stdio", "command": "x", "args": []})

        # runtime=None — get_config() fallback should provide thread_id
        await wrapped.coroutine(runtime=None, x=1)

    pool = get_session_pool()
    assert ("server", "from-langgraph-config") in pool._entries


def test_session_pool_tool_sync_wrapper_path_is_safe():
    """Sync wrapper (tool.func) invocation doesn't crash on cross-loop access."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    from deerflow.mcp.tools import _make_session_pool_tool
    from deerflow.tools.sync import make_sync_tool_wrapper

    class Args(BaseModel):
        url: str = Field(..., description="url")

    original_tool = StructuredTool(
        name="playwright_navigate",
        description="Navigate browser",
        args_schema=Args,
        coroutine=AsyncMock(),
        response_format="content_and_artifact",
    )

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=MagicMock(content=[], isError=False, structuredContent=None))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    connection = {"transport": "stdio", "command": "pw", "args": []}

    with patch("langchain_mcp_adapters.sessions.create_session", return_value=mock_cm):
        wrapped = _make_session_pool_tool(original_tool, "playwright", connection)
        # Attach the sync wrapper exactly as get_mcp_tools() does.
        wrapped.func = make_sync_tool_wrapper(wrapped.coroutine, wrapped.name)

        # Call via the sync path (asyncio.run in a worker thread).
        # runtime is not supplied so _extract_thread_id falls back to "default".
        wrapped.func(url="https://example.com")

    mock_session.call_tool.assert_called_once_with("navigate", {"url": "https://example.com"})
