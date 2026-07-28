from deerflow.connectors.registry import get_connector_registry


def test_registry_contains_mysql_and_starrocks():
    registry = get_connector_registry()

    mysql = registry.get("mysql")
    starrocks = registry.get("starrocks")

    assert mysql.category == "database"
    assert starrocks.category == "database"
    assert "database.query" in mysql.capabilities
    assert starrocks.config_schema["query_port"]["default"] == 9030


def test_registry_contains_onedata():
    onedata = get_connector_registry().get("onedata")

    assert onedata.category == "api"
    assert onedata.display_name == "OneData"
    assert "onedata.list_apis" in onedata.capabilities
    assert "onedata.get_params" in onedata.capabilities
    assert "onedata.call_api" in onedata.capabilities
    assert onedata.config_schema == {}


def test_connector_type_safe_dump_hides_adapter_path():
    safe = get_connector_registry().get("mysql").safe_dump()

    assert "adapter" not in safe
    assert safe["type"] == "mysql"
