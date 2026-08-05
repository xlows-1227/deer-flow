from __future__ import annotations

from copy import deepcopy

from deerflow.connectors.errors import ConnectorValidationError
from deerflow.connectors.schemas import (
    DATABASE_QUERY,
    DATABASE_SCHEMA_INSPECT,
    DATABASE_TABLE_SAMPLE,
    ONEDATA_CALL_API,
    ONEDATA_GET_PARAMS,
    ONEDATA_LIST_APIS,
    ConnectorTypeDefinition,
)


class ConnectorRegistry:
    def __init__(self) -> None:
        self._types: dict[str, ConnectorTypeDefinition] = {}

    def register(self, definition: ConnectorTypeDefinition) -> None:
        self._types[definition.type] = definition

    def get(self, type_name: str) -> ConnectorTypeDefinition:
        try:
            return self._types[type_name]
        except KeyError as exc:
            raise ConnectorValidationError(f"Unknown connector type: {type_name}") from exc

    def list(self) -> list[ConnectorTypeDefinition]:
        return sorted(self._types.values(), key=lambda item: item.type)

    def validate_config(self, type_name: str, config: dict) -> dict:
        definition = self.get(type_name)
        result = deepcopy(config)
        for key, spec in definition.config_schema.items():
            required = bool(spec.get("required", False))
            if key not in result:
                if "default" in spec:
                    result[key] = spec["default"]
                elif required:
                    raise ConnectorValidationError(f"Missing required connector config field: {key}")
            if key in result and "enum" in spec and result[key] not in spec["enum"]:
                choices = ", ".join(str(item) for item in spec["enum"])
                raise ConnectorValidationError(f"Invalid connector config field {key}: expected one of {choices}")
        return result


def _database_config_schema(*, default_port: int, port_name: str = "port") -> dict:
    return {
        "host": {"type": "string", "required": True},
        port_name: {"type": "integer", "default": default_port},
        "database": {"type": "string", "required": True},
        "ssl": {"type": "boolean", "default": False},
    }


def _build_default_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    database_capabilities = [DATABASE_QUERY, DATABASE_SCHEMA_INSPECT, DATABASE_TABLE_SAMPLE]
    default_policy = {
        "mode": "read_only",
        "allow_write": False,
        "allow_ddl": False,
        "max_rows": 10000,
        "statement_timeout_ms": 30000,
        "allow_multi_statement": False,
        "require_limit": True,
        "pii_policy": "mask",
    }
    credential_schema = {
        "url": {"type": "secret", "required": False},
        "username": {"type": "string", "required": False},
        "password": {"type": "secret", "required": False},
    }
    registry.register(
        ConnectorTypeDefinition(
            type="mysql",
            category="database",
            display_name="MySQL",
            adapter="deerflow.connectors.adapters.mysql:MySQLConnectorAdapter",
            auth_modes=["connection_url", "password"],
            capabilities=database_capabilities,
            config_schema=_database_config_schema(default_port=3306),
            credential_schema=credential_schema,
            default_policy=default_policy,
        )
    )
    registry.register(
        ConnectorTypeDefinition(
            type="starrocks",
            category="database",
            display_name="StarRocks",
            adapter="deerflow.connectors.adapters.starrocks:StarRocksConnectorAdapter",
            auth_modes=["connection_url", "password"],
            capabilities=database_capabilities,
            config_schema=_database_config_schema(default_port=9030, port_name="query_port"),
            credential_schema=credential_schema,
            default_policy=default_policy,
        )
    )
    registry.register(
        ConnectorTypeDefinition(
            type="onedata",
            category="api",
            display_name="OneData",
            adapter="deerflow.connectors.adapters.onedata:OneDataConnectorAdapter",
            auth_modes=["password"],
            capabilities=[ONEDATA_LIST_APIS, ONEDATA_GET_PARAMS, ONEDATA_CALL_API],
            config_schema={
                "auth_mode": {
                    "type": "string",
                    "default": "legacy_raw",
                    "enum": ["legacy_raw", "hmac_sha256"],
                },
                "response_mode": {
                    "type": "string",
                    "default": "raw",
                    "enum": ["raw", "strict"],
                },
            },
            credential_schema={
                "secretId": {"type": "string", "required": True},
                "secretKey": {"type": "secret", "required": True},
            },
            default_policy={"request_timeout_ms": 30000},
        )
    )
    return registry


_registry = _build_default_registry()


def get_connector_registry() -> ConnectorRegistry:
    return _registry
