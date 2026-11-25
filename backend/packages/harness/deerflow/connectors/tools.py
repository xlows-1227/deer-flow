from __future__ import annotations

from langchain_core.tools import tool

from deerflow.connectors.errors import ConnectorAuthorizationError, ConnectorError
from deerflow.connectors.result_spill import maybe_spill_result
from deerflow.connectors.schemas import (
    DATABASE_QUERY,
    DATABASE_TABLE_SAMPLE,
    ONEDATA_CALL_API,
    ONEDATA_GET_PARAMS,
    ConnectorRuntimeContext,
)
from deerflow.connectors.service import make_connector_service
from deerflow.connectors.sql_safety import extract_tables
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime


def _context(runtime: Runtime | None) -> ConnectorRuntimeContext:
    ctx = runtime.context if runtime is not None else {}
    raw_connector_ids = ctx.get("connector_ids") if ctx else None
    connector_ids = [str(item) for item in raw_connector_ids if item] if isinstance(raw_connector_ids, list) else None
    raw_capabilities = ctx.get("connector_capabilities") if ctx else None
    connector_capabilities = (
        {str(connector_id): [str(capability) for capability in capabilities if capability] for connector_id, capabilities in raw_capabilities.items() if isinstance(capabilities, list)} if isinstance(raw_capabilities, dict) else None
    )
    return ConnectorRuntimeContext(
        user_id=resolve_runtime_user_id(runtime),
        thread_id=str(ctx.get("thread_id")) if ctx and ctx.get("thread_id") else None,
        run_id=str(ctx.get("run_id")) if ctx and ctx.get("run_id") else None,
        agent_id=str(ctx.get("agent_name")) if ctx and ctx.get("agent_name") else None,
        skill_name=str(ctx.get("skill_name")) if ctx and ctx.get("skill_name") else None,
        connector_ids=connector_ids,
        connector_capabilities=connector_capabilities,
    )


def _error_payload(exc: ConnectorError) -> dict:
    return {"error": {"code": exc.code, "message": exc.message, "recoverable": exc.recoverable}}


def _ensure_selected(context: ConnectorRuntimeContext, connector_id: str) -> None:
    selected_ids = set(context.connector_ids or [])
    if selected_ids and connector_id not in selected_ids:
        raise ConnectorAuthorizationError("Connector is not selected for this chat context", recoverable=True)


async def _spill_if_needed(
    payload,
    *,
    context: ConnectorRuntimeContext,
    service,
    connector_id: str,
    tool_name: str,
    capability: str,
    operation: str,
    sql: str | None = None,
    tables: list[str] | None = None,
    extra_meta: dict | None = None,
) -> dict:
    """Keep small results inline; persist oversized ones and return a summary."""
    if not isinstance(payload, dict):
        return payload
    return await maybe_spill_result(
        payload,
        context=context,
        service=service,
        connector_id=connector_id,
        tool_name=tool_name,
        capability=capability,
        operation=operation,
        sql=sql,
        tables=tables,
        extra_meta=extra_meta,
    )


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
        cached = await make_connector_service().get_cached_schema(connector_id, context=context)
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
        service = make_connector_service()
        result = await service.query_database(connector_id, sql, reason=reason, context=context)
        return await _spill_if_needed(
            result.model_dump(),
            context=context,
            service=service,
            connector_id=connector_id,
            tool_name="query_database",
            capability=DATABASE_QUERY,
            operation="query",
            sql=sql,
            tables=extract_tables(sql),
        )
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
        service = make_connector_service()
        result = await service.sample_database_table(connector_id, schema=schema_name, table=table, limit=limit, context=context)
        return await _spill_if_needed(
            result.model_dump(),
            context=context,
            service=service,
            connector_id=connector_id,
            tool_name="sample_database_table",
            capability=DATABASE_TABLE_SAMPLE,
            operation="sample",
            tables=[f"{schema_name}.{table}"],
            extra_meta={"schema": schema_name, "table": table, "limit": limit},
        )
    except ConnectorError as exc:
        return _error_payload(exc)


@tool("call_connector_action", parse_docstring=True)
async def call_connector_action_tool(
    runtime: Runtime,
    connector_id: str,
    capability: str,
    action_args: dict | None = None,
    reason: str = "",
) -> dict:
    """Call an authorized connector capability through the generic connector action interface.

    Use this for non-database connector categories or future capabilities that
    are not covered by dedicated database tools. Database query capabilities
    are still routed through the same read-only safety checks.
    For OneData parameter inspection and calls, prefer get_onedata_api_params
    and call_onedata_api because their model-facing schemas are explicit.

    Args:
        connector_id: Connector id returned by list_connectors.
        capability: Capability to invoke, such as document.read or api.call.
        action_args: Capability-specific arguments. For OneData get_params,
            provide apiId with its numeric value. For call_api, provide apiId
            and paramData. call_api resolves callUrl automatically.
        reason: Short reason for audit logging.
    """
    try:
        context = _context(runtime)
        _ensure_selected(context, connector_id)
        service = make_connector_service()
        result = await service.execute_connector_action(
            connector_id,
            capability=capability,
            args=action_args or {},
            reason=reason,
            context=context,
        )
        payload = result.model_dump() if hasattr(result, "model_dump") else result
        sql_text = action_args.get("sql") if isinstance(action_args, dict) else None
        return await _spill_if_needed(
            payload,
            context=context,
            service=service,
            connector_id=connector_id,
            tool_name="call_connector_action",
            capability=capability,
            operation="action",
            sql=sql_text,
            tables=extract_tables(sql_text) if isinstance(sql_text, str) and sql_text else None,
        )
    except ConnectorError as exc:
        return _error_payload(exc)


@tool("get_onedata_api_params", parse_docstring=True)
async def get_onedata_api_params_tool(runtime: Runtime, connector_id: str, api_id: int) -> dict:
    """Get the declared request parameters and call URL for one authorized OneData API.

    Use this after listing OneData APIs. Pass the numeric API id exactly as
    returned by onedata.list_apis.

    Args:
        connector_id: OneData connector id returned by list_connectors.
        api_id: Numeric API id, for example 639 for PH pocket sales.
    """
    try:
        context = _context(runtime)
        _ensure_selected(context, connector_id)
        result = await make_connector_service().execute_connector_action(
            connector_id,
            capability=ONEDATA_GET_PARAMS,
            args={"apiId": api_id},
            reason=f"Inspect OneData API {api_id} parameters",
            context=context,
        )
        return result.model_dump() if hasattr(result, "model_dump") else result
    except ConnectorError as exc:
        return _error_payload(exc)


@tool("call_onedata_api", parse_docstring=True)
async def call_onedata_api_tool(
    runtime: Runtime,
    connector_id: str,
    api_id: int,
    param_data: dict | None = None,
    reason: str = "",
    page_size: int | None = None,
    page_num: int | None = None,
    order_by: str | None = None,
    max_size: int | None = None,
    has_total: bool | None = None,
) -> dict:
    """Call an authorized OneData API by id without manually copying its callUrl.

    First use get_onedata_api_params to inspect the exact request parameter
    names and types. Preserve JSON types in param_data: arrays must be arrays,
    not bracketed or comma-separated strings.

    Args:
        connector_id: OneData connector id returned by list_connectors.
        api_id: Numeric API id, for example 639 for PH pocket sales.
        param_data: Request parameters using the exact names from get_onedata_api_params.
        reason: Short reason for audit logging.
        page_size: Optional OneData page size.
        page_num: Optional OneData page number.
        order_by: Optional OneData order-by expression.
        max_size: Optional OneData maximum result size.
        has_total: Whether OneData should calculate a total count.
    """
    try:
        context = _context(runtime)
        _ensure_selected(context, connector_id)
        action_args: dict = {"apiId": api_id, "paramData": param_data or {}}
        for target, value in (
            ("pageSize", page_size),
            ("pageNum", page_num),
            ("orderBy", order_by),
            ("maxSize", max_size),
            ("hasTotal", has_total),
        ):
            if value is not None:
                action_args[target] = value
        service = make_connector_service()
        result = await service.execute_connector_action(
            connector_id,
            capability=ONEDATA_CALL_API,
            args=action_args,
            reason=reason,
            context=context,
        )
        payload = result.model_dump() if hasattr(result, "model_dump") else result
        return await _spill_if_needed(
            payload,
            context=context,
            service=service,
            connector_id=connector_id,
            tool_name="call_onedata_api",
            capability=ONEDATA_CALL_API,
            operation="call_api",
            extra_meta={"api_id": api_id, "page_size": page_size, "page_num": page_num},
        )
    except ConnectorError as exc:
        return _error_payload(exc)
