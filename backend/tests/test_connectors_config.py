from deerflow.config.connectors_config import ConnectorsConfig


def test_connectors_config_defaults_to_disabled():
    config = ConnectorsConfig()

    assert config.enabled is False
    assert config.enabled_types == ["mysql", "starrocks"]
    assert config.secret_store.provider == "env"
    assert config.default_policy.database.mode == "read_only"


def test_connectors_config_accepts_enabled_types():
    config = ConnectorsConfig.model_validate({"enabled": True, "enabled_types": ["mysql"]})

    assert config.enabled is True
    assert config.enabled_types == ["mysql"]
