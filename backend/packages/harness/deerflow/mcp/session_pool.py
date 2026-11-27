"""Persistent MCP session pool for stateful tool calls.

When MCP tools are loaded via langchain-mcp-adapters with ``session=None``,
each tool call creates a new MCP session. For stateful servers like Playwright,
this means browser state (opened pages, filled forms) is lost between calls.

This module provides a session pool that maintains persistent MCP sessions,
scoped by ``(server_name, scope_key)`` — typically scope_key is the thread_id —
so that consecutive tool calls share the same session and server-side state.
Sessions are evicted in LRU order when the pool reaches capacity.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any

from mcp import ClientSession

logger = logging.getLogger(__name__)


class MCPSessionPool:
    """Manages persistent MCP sessions scoped by ``(server_name, scope_key)``."""

    MAX_SESSIONS = 256
    SESSION_CLOSE_TIMEOUT = 5.0  # seconds to wait when closing a session via run_coroutine_threadsafe

    def __init__(self) -> None:
        self._entries: OrderedDict[
            tuple[str, str],
            tuple[ClientSession, asyncio.AbstractEventLoop],
        ] = OrderedDict()
        self._context_managers: dict[tuple[str, str], Any] = {}
        # threading.Lock is not bound to any event loop, so it is safe to
        # acquire from both async paths and sync/worker-thread paths.
        self._lock = threading.Lock()
        # Per (key, event loop) async lock serialising lookup → create →
        # register.  Kept per loop because an asyncio.Lock is bound to the
        # loop that first awaits it; cross-loop callers (sync wrapper path)
        # therefore get a separate lock and are handled by the eviction branch.
        self._key_locks: dict[
            tuple[str, str, asyncio.AbstractEventLoop], asyncio.Lock
        ] = {}

    def _get_key_lock(
        self,
        key: tuple[str, str],
        loop: asyncio.AbstractEventLoop,
    ) -> asyncio.Lock:
        """Return the async lock guarding ``key`` for ``loop``."""
        lock_key = (key[0], key[1], loop)
        with self._lock:
            # The sync tool wrapper calls asyncio.run per invocation, so every
            # sync path registers a lock for a loop that is closed right after.
            # A closed loop can never have a waiter, so drop those entries
            # instead of holding a strong reference to the loop forever.
            closed = [entry for entry in self._key_locks if entry[2].is_closed()]
            for entry in closed:
                del self._key_locks[entry]
            lock = self._key_locks.get(lock_key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[lock_key] = lock
            return lock


    async def get_session(
        self,
        server_name: str,
        scope_key: str,
        connection: dict[str, Any],
    ) -> ClientSession:
        """Get or create a persistent MCP session.

        If an existing session was created in a different event loop (e.g.
        the sync-wrapper path), it is closed and replaced with a fresh one
        in the current loop.

        Args:
            server_name: MCP server name.
            scope_key: Isolation key (typically thread_id).
            connection: Connection configuration for ``create_session``.

        Returns:
            An initialized ``ClientSession``.
        """
        key = (server_name, scope_key)
        current_loop = asyncio.get_running_loop()
        key_lock = self._get_key_lock(key, current_loop)

        # Serialise "look up → create → register" for this key so concurrent
        # tool calls in the same scope cannot both create a session.  Before
        # this lock the loser was silently overwritten and later closed by the
        # GC in a foreign task, raising
        # "Attempted to exit cancel scope in a different task it was entered in".
        async with key_lock:
            # Phase 1: inspect/mutate the registry under the thread lock (no awaits).
            cms_to_close: list[tuple[tuple[str, str], Any]] = []
            with self._lock:
                if key in self._entries:
                    session, loop = self._entries[key]
                    if loop is current_loop:
                        self._entries.move_to_end(key)
                        return session
                    # Session belongs to a different event loop – evict it.
                    cm = self._context_managers.pop(key, None)
                    self._entries.pop(key)
                    if cm is not None:
                        cms_to_close.append((key, cm))

                # Evict LRU entries when at capacity.
                while len(self._entries) >= self.MAX_SESSIONS:
                    oldest_key = next(iter(self._entries))
                    cm = self._context_managers.pop(oldest_key, None)
                    self._entries.pop(oldest_key)
                    if cm is not None:
                        cms_to_close.append((oldest_key, cm))

            # Phase 2: close evicted sessions explicitly (never leave them to
            # the GC).  Known risk: a session created in another task/loop may
            # refuse __aexit__ here with an anyio cancel-scope error; _close_cm
            # logs it instead of letting it escape.
            for close_key, cm in cms_to_close:
                if close_key == key:
                    logger.warning(
                        "Replaced existing MCP session for %s/%s (created in a different event loop); closing it explicitly",
                        server_name,
                        scope_key,
                    )
                await self._close_cm(close_key, cm)

            from langchain_mcp_adapters.sessions import create_session

            cm = create_session(connection)
            session = await cm.__aenter__()
            await session.initialize()

            # Phase 3: register the new session under the lock.  Cross-loop
            # callers cannot share the per-key asyncio lock, so a race can
            # still land here; close the replaced session explicitly rather
            # than dropping it (same cross-task caveat as Phase 2).
            replaced_cm: Any = None
            with self._lock:
                if self._entries.get(key) is not None:
                    replaced_cm = self._context_managers.get(key)
                self._entries[key] = (session, current_loop)
                self._context_managers[key] = cm
            if replaced_cm is not None:
                logger.warning(
                    "Replaced existing MCP session for %s/%s (concurrent cross-loop creation); closing the replaced session explicitly",
                    server_name,
                    scope_key,
                )
                await self._close_cm(key, replaced_cm)
            logger.info("Created persistent MCP session for %s/%s", server_name, scope_key)
            return session


    # ------------------------------------------------------------------
    # Cleanup helpers
    # ------------------------------------------------------------------

    async def _close_cm(self, key: tuple[str, str], cm: Any) -> None:
        """Close a single context manager (must be called WITHOUT the lock).

        Known risk: MCP streamable-HTTP sessions own an anyio task group, so
        ``__aexit__`` only succeeds in the task that entered the context.  When
        a session is evicted/replaced from another task this call can fail with
        "Attempted to exit cancel scope in a different task"; the error is
        logged (with the session key) rather than raised, so the caller can
        continue — the alternative is leaving the context manager to the GC,
        which surfaces the same error as an unretrieved task exception.

        The close is bounded by ``SESSION_CLOSE_TIMEOUT`` because callers hold
        the per-key lock: a hung ``__aexit__`` would otherwise block every
        later ``get_session`` for the same key.
        """
        try:
            await asyncio.wait_for(
                cm.__aexit__(None, None, None),
                timeout=self.SESSION_CLOSE_TIMEOUT,
            )
        except TimeoutError:
            logger.warning(
                "Timed out closing MCP session %s after %.1fs",
                key,
                self.SESSION_CLOSE_TIMEOUT,
            )
        except Exception:
            logger.warning("Error closing MCP session %s", key, exc_info=True)

    async def close_scope(self, scope_key: str) -> None:
        """Close all sessions for a given scope (e.g. thread_id)."""
        with self._lock:
            keys = [k for k in self._entries if k[1] == scope_key]
            cms = [(k, self._context_managers.pop(k, None)) for k in keys]
            for k in keys:
                self._entries.pop(k, None)
        for key, cm in cms:
            if cm is not None:
                await self._close_cm(key, cm)

    async def close_server(self, server_name: str) -> None:
        """Close all sessions for a given server."""
        with self._lock:
            keys = [k for k in self._entries if k[0] == server_name]
            cms = [(k, self._context_managers.pop(k, None)) for k in keys]
            for k in keys:
                self._entries.pop(k, None)
        for key, cm in cms:
            if cm is not None:
                await self._close_cm(key, cm)

    async def close_all(self) -> None:
        """Close every managed session."""
        with self._lock:
            cms = list(self._context_managers.items())
            self._context_managers.clear()
            self._entries.clear()
        for key, cm in cms:
            await self._close_cm(key, cm)

    def close_all_sync(self) -> None:
        """Close all sessions using their owning event loops (synchronous).

        Each session is closed on the loop it was created in, avoiding
        cross-loop resource leaks.  Safe to call from any thread without an
        active event loop.

        Known risk: ``run_coroutine_threadsafe`` runs ``__aexit__`` in a *new*
        task on the owning loop, which is not necessarily the task that created
        the MCP session; anyio may then report a cancel-scope error.  That error
        is caught and logged at DEBUG (best-effort teardown), and the context
        manager is still released rather than left to the GC.
        """
        with self._lock:
            entries = list(self._entries.items())
            cms = dict(self._context_managers)
            self._entries.clear()
            self._context_managers.clear()

        for key, (_, loop) in entries:
            cm = cms.get(key)
            if cm is None or loop.is_closed():
                continue
            try:
                if loop.is_running():
                    # Schedule on the owning loop from this (different) thread.
                    future = asyncio.run_coroutine_threadsafe(cm.__aexit__(None, None, None), loop)
                    future.result(timeout=self.SESSION_CLOSE_TIMEOUT)
                else:
                    loop.run_until_complete(cm.__aexit__(None, None, None))
            except Exception:
                logger.debug("Error closing MCP session %s during sync close", key, exc_info=True)

    def close_servers_by_prefix(self, prefix: str) -> None:
        """Synchronously close every session whose server name starts with ``prefix``.

        Used to tear down a user's namespaced sessions (``user_id::server``)
        when their MCP configuration changes. Like :meth:`close_all_sync`, each
        session is closed on the event loop that owns it, so this is safe to
        call from any thread without an active event loop and avoids the
        cross-loop resource leaks that scheduling ``create_task`` on the
        current loop would cause.

        Known risk (same as :meth:`close_all_sync`): the close coroutine runs
        in a new task on the owning loop, so an MCP session may reject
        ``__aexit__`` with an anyio cancel-scope error; it is logged at DEBUG
        and the context manager is still released.
        """
        with self._lock:
            entries = [(k, v) for k, v in self._entries.items() if k[0].startswith(prefix)]
            cms = {k: self._context_managers.pop(k, None) for k, _ in entries}
            for k, _ in entries:
                self._entries.pop(k, None)

        for key, (_, loop) in entries:
            cm = cms.get(key)
            if cm is None or loop.is_closed():
                continue
            try:
                if loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(cm.__aexit__(None, None, None), loop)
                    future.result(timeout=self.SESSION_CLOSE_TIMEOUT)
                else:
                    loop.run_until_complete(cm.__aexit__(None, None, None))
            except Exception:
                logger.debug("Error closing MCP session %s by prefix", key, exc_info=True)


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_pool: MCPSessionPool | None = None
_pool_lock = threading.Lock()


def get_session_pool() -> MCPSessionPool:
    """Return the global session-pool singleton."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = MCPSessionPool()
    return _pool


def reset_session_pool() -> None:
    """Reset the singleton (for tests)."""
    global _pool
    _pool = None
