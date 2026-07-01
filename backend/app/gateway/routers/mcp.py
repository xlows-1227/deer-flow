import logging

from fastapi import APIRouter, HTTPException

from deerflow.config.extensions_config import get_extensions_config
from deerflow.extensions_user.mcp_service import (
    UserMcpNotFoundError,
    UserMcpPersistenceError,
    UserMcpValidationError,
    make_user_mcp_service,
)
from deerflow.extensions_user.schemas import (
    McpConfigResponse,
    McpServerCreateRequest,
    McpServerEnabledRequest,
    McpServerRecord,
    McpServerUpdateRequest,
)
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["mcp"])


def _user_id() -> str:
    return get_effective_user_id()


def _system_servers():
    return get_extensions_config().mcp_servers


@router.get(
    "/mcp/config",
    response_model=McpConfigResponse,
    summary="Get MCP Configuration",
    description="Retrieve the current user's MCP server configurations.",
)
async def get_mcp_configuration() -> McpConfigResponse:
    user_id = _user_id()
    try:
        return await make_user_mcp_service().get_config_view(user_id, _system_servers())
    except UserMcpPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put(
    "/mcp/servers/{name}/enabled",
    response_model=McpServerRecord,
    summary="Update MCP server enabled state for current user",
)
async def set_mcp_server_enabled(name: str, request: McpServerEnabledRequest) -> McpServerRecord:
    user_id = _user_id()
    try:
        return await make_user_mcp_service().set_server_enabled(user_id, name, request, _system_servers())
    except UserMcpNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UserMcpPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/mcp/servers",
    response_model=McpServerRecord,
    status_code=201,
    summary="Create a user-owned MCP server",
)
async def create_mcp_server(request: McpServerCreateRequest) -> McpServerRecord:
    user_id = _user_id()
    try:
        return await make_user_mcp_service().create_server(user_id, request, _system_servers())
    except UserMcpValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserMcpPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put(
    "/mcp/servers/{name}",
    response_model=McpServerRecord,
    summary="Update a user-owned MCP server",
)
async def update_mcp_server(name: str, request: McpServerUpdateRequest) -> McpServerRecord:
    user_id = _user_id()
    try:
        return await make_user_mcp_service().update_server(user_id, name, request)
    except UserMcpNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UserMcpValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserMcpPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete(
    "/mcp/servers/{name}",
    status_code=204,
    summary="Delete a user-owned MCP server",
)
async def delete_mcp_server(name: str) -> None:
    user_id = _user_id()
    try:
        await make_user_mcp_service().delete_server(user_id, name)
    except UserMcpNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UserMcpPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
