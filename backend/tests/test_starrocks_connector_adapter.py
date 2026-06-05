from deerflow.connectors.adapters.starrocks import StarRocksConnectorAdapter
from deerflow.connectors.schemas import ConnectorCredentialRef, ConnectorInstance


def test_starrocks_adapter_defaults_to_query_port_9030():
    adapter = StarRocksConnectorAdapter()
    instance = ConnectorInstance(
        id="c1",
        name="ads",
        type="starrocks",
        config={"host": "sr-fe.local", "database": "ads"},
        credential=ConnectorCredentialRef(ref="SECRET"),
    )

    url = adapter.build_url(instance, {"username": "app", "password": "pw"})

    assert url == "mysql+asyncmy://app:pw@sr-fe.local:9030/ads"


def test_starrocks_adapter_normalizes_largeint():
    assert StarRocksConnectorAdapter().normalize_type("LARGEINT") == "int128"
