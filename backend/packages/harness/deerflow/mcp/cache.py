"""Cache for MCP tools to avoid repeated loading."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from deerflow.config.effective_config import extensions_config_fingerprint
from deerflow.config.extensions_config import ExtensionsConfig

logger = logging.getLogger(__name__)


@dataclass
class _McpToolsCacheEntry:
    tools: list[BaseTool]
    fingerprint: str
    initialized: bool = True


_initialization_lock = asyncio.Lock()
_cache_by_user: dict[str, _McpToolsCacheEntry] = {}


def _cache_key(user_id: str | None) -> str:
    return user_id or "default"


def invalidate_mcp_tools_cache_for_user(user_id: str) -> None:
    """Drop the cached MCP tools for ``user_id`` and close their live sessions.

    The MCP tools cache (``_cache_by_user``) has no TTL by design: it is kept
    fresh via this push-based invalidation, triggered whenever a user's
    extensions config changes. The matching persistent MCP sessions are
    namespaced as ``"<user_id>::<server>"`` in the session pool, so we close
    them by prefix.

    Cross-process note: this only affects the current process. Standard
    deployments (``make dev`` / Docker / prod) run the agent runtime inside
    the Gateway process, so the write and the agent share this cache. The
    LangGraph Studio dev workflow runs the graph in a separate process that
    does not receive this push; there the cache self-heals within
    ``_USER_EXTENSIONS_CACHE_TTL_SECONDS`` (see ``effective_config.py``) once
    the fingerprint is recomputed.
    """
    _cache_by_user.pop(_cache_key(user_id), None)
    try:
        from deerflow.mcp.session_pool import get_session_pool

        pool = get_session_pool()
        pool.close_servers_by_prefix(f"{_cache_key(user_id)}::")
    except Exception:
        logger.debug("Could not close MCP sessions for user %s", user_id, exc_info=True)


def reset_mcp_tools_cache() -> None:
    """Reset all MCP tools caches (for tests or global reload)."""
    global _cache_by_user
    _cache_by_user = {}
    try:
        from deerflow.mcp.session_pool import get_session_pool, reset_session_pool

        pool = get_session_pool()
        pool.close_all_sync()
        reset_session_pool()
    except Exception:
        logger.debug("Could not reset MCP session pool on cache reset", exc_info=True)
    logger.info("MCP tools cache reset")


async def initialize_mcp_tools(
    *,
    user_id: str | None = None,
    extensions_config: ExtensionsConfig | None = None,
) -> list[BaseTool]:
    """Initialize and cache MCP tools for a user."""
    return await _ensure_cached_tools(user_id=user_id, extensions_config=extensions_config)


async def _ensure_cached_tools(
    *,
    user_id: str | None = None,
    extensions_config: ExtensionsConfig | None = None,
) -> list[BaseTool]:
    from deerflow.config.extensions_config import ExtensionsConfig as ExtensionsConfigClass
    from deerflow.mcp.tools import get_mcp_tools

    key = _cache_key(user_id)
    config = extensions_config or ExtensionsConfigClass.from_file()
    fingerprint = extensions_config_fingerprint(config)
    existing = _cache_by_user.get(key)
    if existing is not None and existing.fingerprint == fingerprint and existing.initialized:
        return existing.tools

    async with _initialization_lock:
        existing = _cache_by_user.get(key)
        if existing is not None and existing.fingerprint == fingerprint and existing.initialized:
            return existing.tools

        logger.info("Initializing MCP tools for user %s...", key)
        tools = await get_mcp_tools(extensions_config=config, user_id=key)
        _cache_by_user[key] = _McpToolsCacheEntry(tools=tools, fingerprint=fingerprint)
        logger.info("MCP tools initialized for user %s: %s tool(s)", key, len(tools))
        return tools


def get_cached_mcp_tools(
    *,
    user_id: str | None = None,
    extensions_config: ExtensionsConfig | None = None,
) -> list[BaseTool]:
    """Get cached MCP tools with lazy initialization."""
    key = _cache_key(user_id)
    config = extensions_config
    if config is not None:
        fingerprint = extensions_config_fingerprint(config)
        existing = _cache_by_user.get(key)
        if existing is not None and existing.fingerprint == fingerprint:
            return existing.tools

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    _ensure_cached_tools(user_id=user_id, extensions_config=extensions_config),
                )
                return future.result()
        return loop.run_until_complete(_ensure_cached_tools(user_id=user_id, extensions_config=extensions_config))
    except RuntimeError:
        try:
            return asyncio.run(_ensure_cached_tools(user_id=user_id, extensions_config=extensions_config))
        except Exception:
            logger.exception("Failed to lazy-initialize MCP tools for user %s", key)
            return []
