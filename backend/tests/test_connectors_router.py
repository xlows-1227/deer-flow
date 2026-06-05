from __future__ import annotations

from fastapi.testclient import TestClient

from _router_auth_helpers import make_authed_test_app
from app.gateway.app import create_app
from app.gateway.routers import connectors


class _Model:
    def __init__(self, **values):
        self.values = values

    def model_dump(self):
        return dict(self.values)


class _FakeConnectorService:
    async def list_connector_types(self):
        return [{"type": "mysql"}, {"type": "starrocks"}]

    async def create_connector(self, values, *, owner_id):
        return _Model(id="conn_1", owner_id=owner_id, status="active", **values)

    async def list_connectors(self, *, owner_id, include_disabled=True):  # noqa: ARG002
        return [
            _Model(id="conn_1", name="orders", type="mysql", credential={"provider": "env", "ref": "MYSQL_URL"}),
            _Model(
                id="conn_2",
                name="orders_inline",
                type="mysql",
                credential={
                    "provider": "inline",
                    "ref": "gAAAAAencrypted-blob",
                    "username": "readonly",
                },
            ),
        ]

    async def get_connector(self, connector_id, *, owner_id):  # noqa: ARG002
        if connector_id == "conn_2":
            return _Model(
                id="conn_2",
                name="orders_inline",
                type="mysql",
                credential={
                    "provider": "inline",
                    "ref": "gAAAAAencrypted-blob",
                    "username": "readonly",
                },
            )
        return _Model(
            id="conn_1",
            name="orders",
            type="mysql",
            credential={"provider": "env", "ref": "MYSQL_URL"},
        )

    async def test_connector_config(self, *, type_name, config, credential, default_policy, context):  # noqa: ARG002
        return _Model(status="ok", latency_ms=12, capabilities=["database.query"])

    async def test_connector_config_for_instance(self, connector_id, *, values, context, owner_id):  # noqa: ARG002
        return _Model(status="ok", latency_ms=15, capabilities=["database.query"])


def test_connectors_router_basic_paths(monkeypatch):
    monkeypatch.setattr(connectors, "make_connector_service", lambda: _FakeConnectorService())
    app = make_authed_test_app()
    app.include_router(connectors.router)

    with TestClient(app) as client:
        types = client.get("/api/connector-types")
        assert types.status_code == 200
        assert types.json()["connector_types"][0]["type"] == "mysql"

        created = client.post(
            "/api/connectors",
            json={
                "name": "orders",
                "type": "mysql",
                "config": {"host": "db", "database": "orders"},
                "credential": {"provider": "env", "ref": "MYSQL_URL"},
            },
        )
        assert created.status_code == 201
        assert created.json()["id"] == "conn_1"
        assert created.json()["credential"] == {"provider": "env", "ref": "MYSQL_URL"}
        assert "value" not in created.json().get("credential", {})

        listed = client.get("/api/connectors")
        assert listed.status_code == 200
        env_connector = listed.json()["connectors"][0]
        assert env_connector["credential"] == {"provider": "env", "ref": "MYSQL_URL"}
        assert "value" not in env_connector.get("credential", {})

        inline_connector = listed.json()["connectors"][1]
        assert inline_connector["credential"] == {
            "provider": "inline",
            "ref": "gAAAAAencrypted-blob",
            "username": "readonly",
            "has_password": True,
        }
        # The plaintext password must never appear on the wire.
        assert "password" not in inline_connector["credential"]

        # Single-connector fetch also returns the safe shape.
        single = client.get("/api/connectors/conn_2")
        assert single.status_code == 200
        assert single.json()["credential"]["username"] == "readonly"
        assert single.json()["credential"]["has_password"] is True
        assert "password" not in single.json()["credential"]

        draft_test = client.post(
            "/api/connector-types/mysql/test",
            json={
                "config": {"host": "db", "database": "orders"},
                "credential": {"provider": "env", "ref": "MYSQL_URL"},
            },
        )
        assert draft_test.status_code == 200
        assert draft_test.json()["status"] == "ok"

        edited_test = client.post(
            "/api/connectors/conn_1/test-config",
            json={"config": {"host": "db2", "database": "orders"}},
        )
        assert edited_test.status_code == 200
        assert edited_test.json()["latency_ms"] == 15


def test_gateway_app_mounts_connectors_router():
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/connector-types" in paths
    assert "/api/connectors" in paths
    assert "/api/connectors/{connector_id}/actions" in paths
    assert "/api/connector-types/{type_name}/test" in paths
    assert "/api/connectors/{connector_id}/test-config" in paths
