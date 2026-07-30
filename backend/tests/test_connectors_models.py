from sqlalchemy import Text

from deerflow.connectors.secrets import InlineSecretStore
from deerflow.persistence.base import Base
from deerflow.persistence.connector.model import ConnectorAuditLogRow, ConnectorGrantRow, ConnectorInstanceRow, ConnectorMetadataCacheRow


def test_connector_tables_are_registered():
    assert ConnectorInstanceRow.__tablename__ in Base.metadata.tables
    assert ConnectorGrantRow.__tablename__ in Base.metadata.tables
    assert ConnectorMetadataCacheRow.__tablename__ in Base.metadata.tables
    assert ConnectorAuditLogRow.__tablename__ in Base.metadata.tables


def test_credential_ref_column_fits_fernet_token():
    """Inline credentials store a Fernet blob in credential_ref; must not be VARCHAR(128)."""
    token = InlineSecretStore().encrypt({"username": "mock-secret-id", "password": "mock-secret-key"})
    assert len(token) > 128

    column = ConnectorInstanceRow.__table__.c.credential_ref
    assert isinstance(column.type, Text)
