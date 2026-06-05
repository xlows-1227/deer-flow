from deerflow.connectors.adapters.mysql import MySQLConnectorAdapter
from deerflow.connectors.schemas import ConnectorCredentialRef, ConnectorInstance


def test_mysql_adapter_builds_url_from_split_credentials():
    adapter = MySQLConnectorAdapter()
    instance = ConnectorInstance(
        id="c1",
        name="orders",
        type="mysql",
        config={"host": "mysql.local", "port": 3307, "database": "orders"},
        credential=ConnectorCredentialRef(ref="SECRET"),
    )

    url = adapter.build_url(instance, {"username": "app", "password": "pw"})

    assert url == "mysql+asyncmy://app:pw@mysql.local:3307/orders"


def test_mysql_adapter_uses_information_schema_for_introspection():
    adapter = MySQLConnectorAdapter()
    instance = ConnectorInstance(
        id="c1",
        name="orders",
        type="mysql",
        config={"host": "mysql.local", "database": "orders"},
        credential=ConnectorCredentialRef(ref="SECRET"),
    )

    sql = adapter.introspection_sql(instance)

    assert "information_schema.columns" in sql
    assert "orders" in sql
