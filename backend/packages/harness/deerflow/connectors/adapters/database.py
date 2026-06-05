from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from deerflow.connectors.errors import ConnectorExecutionError, ConnectorValidationError
from deerflow.connectors.schemas import ConnectorInstance, ConnectorMetadata, ConnectorRuntimeContext, ConnectorTestResult, DatabasePolicy, QueryColumn, QueryResult
from deerflow.connectors.secrets import redact_secret_text

EngineFactory = Callable[[str], AsyncEngine]
logger = logging.getLogger(__name__)


class BaseMySQLProtocolDatabaseAdapter:
    type = "database"
    display_name = "Database"
    sqlalchemy_driver = "mysql+asyncmy"
    default_port_key = "port"
    default_port = 3306
    dialect = "mysql"

    def __init__(self, *, engine_factory: EngineFactory | None = None) -> None:
        self._engine_factory = engine_factory or self._default_engine_factory
        self._engines: dict[str, AsyncEngine] = {}

    def _default_engine_factory(self, url: str) -> AsyncEngine:
        return create_async_engine(url, pool_pre_ping=True)

    def _get_engine(self, url: str) -> AsyncEngine:
        if url not in self._engines:
            self._engines[url] = self._engine_factory(url)
        return self._engines[url]

    def build_url(self, instance: ConnectorInstance, secrets: dict[str, Any]) -> str:
        if secrets.get("url"):
            return str(secrets["url"])
        username = secrets.get("username")
        password = secrets.get("password")
        if not username or not password:
            raise ConnectorValidationError("Database connector requires either a URL secret or username/password secrets")
        host = instance.config.get("host")
        database = instance.config.get("database")
        if not host or not database:
            raise ConnectorValidationError("Database connector requires host and database config")
        port = int(instance.config.get(self.default_port_key) or self.default_port)
        return f"{self.sqlalchemy_driver}://{quote_plus(str(username))}:{quote_plus(str(password))}@{host}:{port}/{database}"

    def _secret_payload(self, secrets: dict[str, Any]) -> dict[str, Any]:
        if "url" in secrets:
            return {"url": secrets["url"]}
        if "value" in secrets:
            return {"url": secrets["value"]}
        return secrets

    async def test(self, instance: ConnectorInstance, secrets: dict[str, Any]) -> ConnectorTestResult:
        start = time.perf_counter()
        engine = self._engine_factory(self.build_url(instance, self._secret_payload(secrets)))
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return ConnectorTestResult(status="ok", latency_ms=int((time.perf_counter() - start) * 1000), capabilities=["database.query", "database.schema.inspect", "database.table.sample"])
        except Exception as exc:  # noqa: BLE001
            raise ConnectorExecutionError(redact_secret_text(str(exc)), recoverable=True) from exc
        finally:
            await engine.dispose()

    async def introspect(self, instance: ConnectorInstance, secrets: dict[str, Any]) -> ConnectorMetadata:
        engine = self._engine_factory(self.build_url(instance, self._secret_payload(secrets)))
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(text(self.introspection_sql(instance)))
                tables: list[dict[str, Any]] = []
                schemas: dict[str, dict[str, Any]] = {}
                for row in rows.mappings():
                    schema_name = str(row.get("table_schema"))
                    table_name = str(row.get("table_name"))
                    column = {
                        "name": row.get("column_name"),
                        "type": self.normalize_type(row.get("data_type")),
                        "nullable": row.get("is_nullable"),
                        "comment": row.get("column_comment"),
                    }
                    schema_entry = schemas.setdefault(schema_name, {"name": schema_name, "tables": []})
                    table_entry = next((item for item in schema_entry["tables"] if item["name"] == table_name), None)
                    if table_entry is None:
                        table_entry = {"schema": schema_name, "name": table_name, "columns": []}
                        schema_entry["tables"].append(table_entry)
                        tables.append({"schema": schema_name, "name": table_name})
                    table_entry["columns"].append(column)
                return ConnectorMetadata(schemas=list(schemas.values()), tables=tables)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorExecutionError(redact_secret_text(str(exc)), recoverable=True) from exc
        finally:
            await engine.dispose()

    def introspection_sql(self, instance: ConnectorInstance) -> str:
        database = str(instance.config.get("database") or "").replace("'", "''")
        return (
            "SELECT table_schema, table_name, column_name, data_type, is_nullable, column_comment "
            "FROM information_schema.columns "
            f"WHERE table_schema = '{database}' "
            "ORDER BY table_schema, table_name, ordinal_position"
        )

    def normalize_type(self, value: Any) -> str | None:
        return str(value).lower() if value is not None else None

    async def execute(
        self,
        instance: ConnectorInstance,
        capability: str,
        args: dict[str, Any],
        policy: DatabasePolicy | dict[str, Any],
        context: ConnectorRuntimeContext,
        *,
        secrets: dict[str, Any] | None = None,
    ) -> QueryResult | ConnectorMetadata:
        db_policy = policy if isinstance(policy, DatabasePolicy) else DatabasePolicy.model_validate(policy)
        if capability == "database.query":
            return await self.query(instance, str(args["sql"]), db_policy, context, secrets=secrets or dict(args.get("secrets") or {}))
        if capability == "database.schema.inspect":
            return await self.introspect(instance, secrets or dict(args.get("secrets") or {}))
        raise ConnectorValidationError(f"Unsupported database capability: {capability}")

    async def query(
        self,
        instance: ConnectorInstance,
        sql: str,
        policy: DatabasePolicy,
        context: ConnectorRuntimeContext,  # noqa: ARG002
        *,
        secrets: dict[str, Any] | None = None,
    ) -> QueryResult:
        start = time.perf_counter()
        url = self.build_url(instance, self._secret_payload(secrets or {}))
        engine = self._get_engine(url)
        try:
            async with engine.connect() as conn:
                await self.before_query(conn, policy)
                result = await asyncio.wait_for(conn.execute(text(sql)), timeout=policy.statement_timeout_ms / 1000)
                columns = [QueryColumn(name=str(col), type=None) for col in result.keys()]
                rows: list[list[Any]] = []
                truncated = False
                for row in result:
                    if len(rows) >= policy.max_rows:
                        truncated = True
                        break
                    rows.append([self._coerce_cell(value) for value in row])
                return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated, elapsed_ms=int((time.perf_counter() - start) * 1000))
        except Exception as exc:  # noqa: BLE001
            raise ConnectorExecutionError(redact_secret_text(str(exc)), recoverable=True) from exc

    async def before_query(self, conn: Any, policy: DatabasePolicy) -> None:
        del policy
        try:
            await conn.execute(text("START TRANSACTION READ ONLY"))
        except Exception:
            logger.warning("Failed to set READ ONLY transaction; proceeding without explicit read-only guard")

    def _coerce_cell(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            if isinstance(value, str) and len(value) > 4096:
                return value[:4096]
            return value
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
