from __future__ import annotations

from typing import Any, Protocol

from deerflow.connectors.schemas import ConnectorInstance, ConnectorMetadata, ConnectorRuntimeContext, ConnectorTestResult, DatabasePolicy, QueryResult


class ConnectorAdapter(Protocol):
    type: str

    async def test(self, instance: ConnectorInstance, secrets: dict[str, Any]) -> ConnectorTestResult:
        ...

    async def introspect(self, instance: ConnectorInstance, secrets: dict[str, Any]) -> ConnectorMetadata:
        ...

    async def execute(
        self,
        instance: ConnectorInstance,
        capability: str,
        args: dict[str, Any],
        policy: dict[str, Any],
        context: ConnectorRuntimeContext,
        *,
        secrets: dict[str, Any] | None = None,
    ) -> Any:
        ...


class DatabaseConnectorAdapter(ConnectorAdapter, Protocol):
    async def query(
        self,
        instance: ConnectorInstance,
        sql: str,
        policy: DatabasePolicy,
        context: ConnectorRuntimeContext,
        *,
        secrets: dict[str, Any] | None = None,
    ) -> QueryResult:
        ...
