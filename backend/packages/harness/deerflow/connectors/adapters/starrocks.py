from __future__ import annotations

from typing import Any

from deerflow.connectors.adapters.database import BaseMySQLProtocolDatabaseAdapter


class StarRocksConnectorAdapter(BaseMySQLProtocolDatabaseAdapter):
    type = "starrocks"
    display_name = "StarRocks"
    default_port_key = "query_port"
    default_port = 9030
    dialect = "mysql"

    def normalize_type(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).lower()
        aliases = {
            "largeint": "int128",
            "datetime": "timestamp",
        }
        return aliases.get(normalized, normalized)
