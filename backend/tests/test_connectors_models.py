from deerflow.persistence.base import Base
from deerflow.persistence.connector.model import ConnectorAuditLogRow, ConnectorGrantRow, ConnectorInstanceRow, ConnectorMetadataCacheRow


def test_connector_tables_are_registered():
    assert ConnectorInstanceRow.__tablename__ in Base.metadata.tables
    assert ConnectorGrantRow.__tablename__ in Base.metadata.tables
    assert ConnectorMetadataCacheRow.__tablename__ in Base.metadata.tables
    assert ConnectorAuditLogRow.__tablename__ in Base.metadata.tables
