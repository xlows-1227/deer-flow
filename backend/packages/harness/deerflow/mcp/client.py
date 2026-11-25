"""MCP client using langchain-mcp-adapters."""

import asyncio
import logging
import re
from typing import Any

from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig

logger = logging.getLogger(__name__)

# Tool names are exposed as ``{server_name}_{tool_name}``; an invalid server
# name makes every tool from that server unusable for the model.
_MCP_SERVER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")


def build_server_params(server_name: str, config: McpServerConfig) -> dict[str, Any]:
    """Build server parameters for MultiServerMCPClient.

    Args:
        server_name: Name of the MCP server.
        config: Configuration for the MCP server.

    Returns:
        Dictionary of server parameters for langchain-mcp-adapters.
    """
    transport_type = config.type or "stdio"
    params: dict[str, Any] = {"transport": transport_type}

    if transport_type == "stdio":
        if not config.command:
            raise ValueError(f"MCP server '{server_name}' with stdio transport requires 'command' field")
        params["command"] = config.command
        params["args"] = config.args
        # Add environment variables if present
        if config.env:
            params["env"] = config.env
    elif transport_type in ("sse", "http", "streamable_http"):
        if not config.url:
            raise ValueError(f"MCP server '{server_name}' with {transport_type} transport requires 'url' field")
        params["url"] = config.url
        # Add headers if present
        if config.headers:
            params["headers"] = config.headers
    else:
        raise ValueError(f"MCP server '{server_name}' has unsupported transport type: {transport_type}")

    return params


async def list_server_tools(
    server_name: str,
    config: McpServerConfig,
    *,
    extensions_config: ExtensionsConfig | None = None,
    timeout: float = 20.0,
) -> list[dict[str, str]]:
    """Connect to a single MCP server and return its tool name/description pairs.

    Args:
        server_name: Name of the MCP server.
        config: Configuration for the MCP server.
        extensions_config: Optional effective extensions config used to inject
            initial OAuth ``Authorization`` headers.
        timeout: Maximum time to wait for tool discovery.

    Returns:
        List of ``{"name": ..., "description": ...}`` dictionaries.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    if not _MCP_SERVER_NAME_RE.fullmatch(server_name):
        raise ValueError(f"Invalid MCP server name: {server_name!r}")

    params = build_server_params(server_name, config)
    if extensions_config is not None:
        from deerflow.mcp.oauth import get_initial_oauth_headers

        auth_header = (await get_initial_oauth_headers(extensions_config)).get(server_name)
        if auth_header and params.get("transport") in ("sse", "http", "streamable_http"):
            params["headers"] = {**params.get("headers", {}), "Authorization": auth_header}

    client = MultiServerMCPClient({server_name: params}, tool_name_prefix=True)
    tools = await asyncio.wait_for(client.get_tools(), timeout=timeout)
    return [{"name": tool.name, "description": tool.description or ""} for tool in tools]


def build_servers_config(extensions_config: ExtensionsConfig) -> dict[str, dict[str, Any]]:
    """Build servers configuration for MultiServerMCPClient.

    Args:
        extensions_config: Extensions configuration containing all MCP servers.

    Returns:
        Dictionary mapping server names to their parameters.
    """
    enabled_servers = extensions_config.get_enabled_mcp_servers()

    if not enabled_servers:
        logger.info("No enabled MCP servers found")
        return {}

    servers_config = {}
    for server_name, server_config in enabled_servers.items():
        if not _MCP_SERVER_NAME_RE.fullmatch(server_name):
            logger.warning(
                "Skipping MCP server with invalid name %r (must match ^[A-Za-z][A-Za-z0-9_-]*$)",
                server_name,
            )
            continue
        try:
            servers_config[server_name] = build_server_params(server_name, server_config)
            logger.info(f"Configured MCP server: {server_name}")
        except Exception as e:
            logger.error(f"Failed to configure MCP server '{server_name}': {e}")

    return servers_config
