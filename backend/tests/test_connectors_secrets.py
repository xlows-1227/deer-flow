import pytest

from deerflow.connectors.errors import ConnectorSecretError
from deerflow.connectors.schemas import ConnectorCredentialRef
from deerflow.connectors.secrets import EnvSecretStore, redact_secret_text


def test_env_secret_store_resolves_ref(monkeypatch):
    monkeypatch.setenv("MYSQL_TEST_URL", "mysql://user:pass@example/db")

    secret = EnvSecretStore().get_secret(ConnectorCredentialRef(provider="env", ref="MYSQL_TEST_URL"))

    assert secret.value.endswith("/db")


def test_env_secret_store_missing_ref_raises(monkeypatch):
    monkeypatch.delenv("MISSING_CONNECTOR_SECRET", raising=False)

    with pytest.raises(ConnectorSecretError):
        EnvSecretStore().get_secret(ConnectorCredentialRef(provider="env", ref="MISSING_CONNECTOR_SECRET"))


def test_redact_secret_text_masks_url_password():
    text = redact_secret_text("mysql://root:super-secret@db/orders password=abc")

    assert "super-secret" not in text
    assert "password=***" in text
