import pytest

from deerflow.connectors.errors import ConnectorValidationError
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
    assert onedata.config_schema["auth_mode"]["default"] == "legacy_raw"
    assert onedata.config_schema["auth_mode"]["enum"] == ["legacy_raw", "hmac_sha256"]
    assert onedata.config_schema["response_mode"]["default"] == "raw"


def test_connector_type_safe_dump_hides_adapter_path():
    safe = get_connector_registry().get("mysql").safe_dump()

    assert "adapter" not in safe
    assert safe["type"] == "mysql"


def test_registry_validates_onedata_modes():
    registry = get_connector_registry()

    assert registry.validate_config("onedata", {}) == {
        "auth_mode": "legacy_raw",
        "response_mode": "raw",
    }
    with pytest.raises(ConnectorValidationError, match="auth_mode"):
        registry.validate_config("onedata", {"auth_mode": "unsupported"})
