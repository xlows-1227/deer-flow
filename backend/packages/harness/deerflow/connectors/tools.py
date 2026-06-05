from __future__ import annotations

from langchain_core.tools import tool

from deerflow.connectors.errors import ConnectorAuthorizationError, ConnectorError
from deerflow.connectors.schemas import ConnectorRuntimeContext
from deerflow.connectors.service import make_connector_service
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime


def _context(runtime: Runtime | None) -> ConnectorRuntimeContext:
    ctx = runtime.context if runtime is not None else {}
    raw_connector_ids = ctx.get("connector_ids") if ctx else None
    connector_ids = [str(item) for item in raw_connector_ids if item] if isinstance(raw_connector_ids, list) else None
    return ConnectorRuntimeContext(
        user_id=resolve_runtime_user_id(runtime),
        thread_id=str(ctx.get("thread_id")) if ctx and ctx.get("thread_id") else None,
        run_id=str(ctx.get("run_id")) if ctx and ctx.get("run_id") else None,
        agent_id=str(ctx.get("agent_name")) if ctx and ctx.get("agent_name") else None,
        skill_name=str(ctx.get("skill_name")) if ctx and ctx.get("skill_name") else None,
        connector_ids=connector_ids,
    )


def _error_payload(exc: ConnectorError) -> dict:
    return {"error": {"code": exc.code, "message": exc.message, "recoverable": exc.recoverable}}


def _ensure_selected(context: ConnectorRuntimeContext, connector_id: str) -> None:
    selected_ids = set(context.connector_ids or [])
    if selected_ids and connector_id not in selected_ids:
        raise ConnectorAuthorizationError("Connector is not selected for this chat context", recoverable=True)


@tool("list_connectors", parse_docstring=True)
async def list_connectors_tool(runtime: Runtime, capability: str | None = None) -> dict:
    """List connector resources available to the current runtime context.

    Args:
        capability: Optional capability filter such as database.query.
    """
    try:
        context = _context(runtime)
        return {"connectors": await make_connector_service().list_available_summaries(context=context, capability=capability)}
    except ConnectorError as exc:
        return _error_payload(exc)


@tool("inspect_connector", parse_docstring=True)
async def inspect_connector_tool(runtime: Runtime, connector_id: str, resource_type: str = "schema") -> dict:
    """Inspect cached connector resources such as database schema metadata.

    Args:
        connector_id: Connector id returned by list_connectors.
        resource_type: Resource type to inspect. The first version supports schema.
    """
    try:
        context = _context(runtime)
        _ensure_selected(context, connector_id)
        if resource_type != "schema":
            return {"error": {"code": "connector.resource.unsupported", "message": "Only schema inspection is supported in v1.", "recoverable": True}}
        cached = await make_connector_service().get_cached_schema(connector_id)
        if cached is None:
            metadata = await make_connector_service().introspect_connector(connector_id, context=context)
            return metadata.model_dump()
        return cached["metadata_json"]
    except ConnectorError as exc:
        return _error_payload(exc)


@tool("query_database", parse_docstring=True)
async def query_database_tool(runtime: Runtime, connector_id: str, sql: str, reason: str) -> dict:
    """Run a read-only SQL query through an authorized database connector.

    Args:
        connector_id: Connector id returned by list_connectors.
        sql: Read-only SELECT SQL.
        reason: Short reason for audit logging.
    """
    try:
        context = _context(runtime)
        _ensure_selected(context, connector_id)
        return (await make_connector_service().query_database(connector_id, sql, reason=reason, context=context)).model_dump()
    except ConnectorError as exc:
        return _error_payload(exc)


@tool("sample_database_table", parse_docstring=True)
async def sample_database_table_tool(runtime: Runtime, connector_id: str, schema_name: str, table: str, limit: int = 20) -> dict:
    """Sample rows from a database table through an authorized connector.

    Args:
        connector_id: Connector id returned by list_connectors.
        schema_name: Database schema name.
        table: Table name.
        limit: Maximum rows to sample, capped at 100.
    """
    try:
        context = _context(runtime)
        _ensure_selected(context, connector_id)
        return (await make_connector_service().sample_database_table(connector_id, schema=schema_name, table=table, limit=limit, context=context)).model_dump()
    except ConnectorError as exc:
        return _error_payload(exc)


@tool("call_connector_action", parse_docstring=True)
async def call_connector_action_tool(runtime: Runtime, connector_id: str, capability: str, args: dict | None = None, reason: str = "") -> dict:
    """Call an authorized connector capability through the generic connector action interface.

    Use this for non-database connector categories or future capabilities that
    are not covered by dedicated database tools. Database query capabilities
    are still routed through the same read-only safety checks.

    Args:
        connector_id: Connector id returned by list_connectors.
        capability: Capability to invoke, such as document.read or api.call.
        args: Capability-specific arguments.
        reason: Short reason for audit logging.
    """
    try:
        context = _context(runtime)
        _ensure_selected(context, connector_id)
        result = await make_connector_service().execute_connector_action(
            connector_id,
            capability=capability,
            args=args or {},
            reason=reason,
            context=context,
        )
        return result.model_dump() if hasattr(result, "model_dump") else result
    except ConnectorError as exc:
        return _error_payload(exc)
