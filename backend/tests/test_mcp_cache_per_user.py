from __future__ import annotations

import pytest

from deerflow.config.effective_config import extensions_config_fingerprint
from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig
from deerflow.mcp import cache as mcp_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    mcp_cache.reset_mcp_tools_cache()
    yield
    mcp_cache.reset_mcp_tools_cache()


def test_extensions_config_fingerprint_changes_when_servers_change():
    base = ExtensionsConfig(mcp_servers={"a": McpServerConfig(enabled=True)})
    changed = ExtensionsConfig(mcp_servers={"a": McpServerConfig(enabled=False)})
    assert extensions_config_fingerprint(base) != extensions_config_fingerprint(changed)


def test_mcp_cache_isolated_by_user_id(monkeypatch):
    calls: list[str] = []

    async def fake_get_mcp_tools(*, extensions_config=None, user_id="default"):
        calls.append(user_id)
        return []

    monkeypatch.setattr("deerflow.mcp.tools.get_mcp_tools", fake_get_mcp_tools)

    config_a = ExtensionsConfig(mcp_servers={"srv": McpServerConfig(enabled=True)})
    config_b = ExtensionsConfig(mcp_servers={"srv": McpServerConfig(enabled=True, description="other")})

    tools_a1 = mcp_cache.get_cached_mcp_tools(user_id="user-a", extensions_config=config_a)
    tools_a2 = mcp_cache.get_cached_mcp_tools(user_id="user-a", extensions_config=config_a)
    tools_b = mcp_cache.get_cached_mcp_tools(user_id="user-b", extensions_config=config_b)

    assert tools_a1 == []
    assert tools_a2 == []
    assert tools_b == []
    assert calls.count("user-a") == 1
    assert calls.count("user-b") == 1

    mcp_cache.invalidate_mcp_tools_cache_for_user("user-a")
    mcp_cache.get_cached_mcp_tools(user_id="user-a", extensions_config=config_a)
    assert calls.count("user-a") == 2
