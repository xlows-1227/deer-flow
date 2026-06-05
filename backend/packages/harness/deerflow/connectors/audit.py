from __future__ import annotations

from typing import Any

from deerflow.connectors.schemas import ConnectorRuntimeContext
from deerflow.persistence.connector import ConnectorRepository


async def write_connector_audit(
    repository: ConnectorRepository,
    *,
    connector_id: str | None,
    connector_type: str | None,
    context: ConnectorRuntimeContext,
    capability: str,
    operation: str,
    decision: str,
    request_summary: dict[str, Any] | None = None,
    result_summary: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    return await repository.append_audit(
        {
            "connector_id": connector_id,
            "connector_type": connector_type,
            "user_id": context.user_id,
            "tenant_id": context.tenant_id,
            "thread_id": context.thread_id,
            "run_id": context.run_id,
            "agent_id": context.agent_id,
            "skill_name": context.skill_name,
            "capability": capability,
            "operation": operation,
            "decision": decision,
            "request_summary_json": request_summary or {},
            "result_summary_json": result_summary or {},
            "error_code": error_code,
            "error_message": error_message,
            "elapsed_ms": elapsed_ms,
        }
    )
