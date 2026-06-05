from __future__ import annotations

from deerflow.connectors.adapters.database import BaseMySQLProtocolDatabaseAdapter


class MySQLConnectorAdapter(BaseMySQLProtocolDatabaseAdapter):
    type = "mysql"
    display_name = "MySQL"
    default_port_key = "port"
    default_port = 3306
    dialect = "mysql"
