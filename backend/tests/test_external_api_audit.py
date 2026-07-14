import hashlib

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.gateway.external.agent_auth import AgentAPIAuthMiddleware
from app.gateway.external.audit import ExternalAuditMiddleware


class AuditRepo:
    def __init__(self):
        self.rows = []

    async def append(self, values):
        self.rows.append(values)
        return values


def test_external_request_gets_request_id_and_metadata_only_audit():
    app = FastAPI()
    repository = AuditRepo()
    app.state.external_audit_repo = repository
    app.add_middleware(ExternalAuditMiddleware)

    @app.post("/api/v1/external/test")
    async def test_route():
        return {"answer": "secret answer"}

    response = TestClient(app).post(
        "/api/v1/external/test",
        headers={
            "Authorization": "Bearer secret-key",
            "X-Request-ID": "request_1234",
            "User-Agent": "client dfk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_secret",
        },
        json={"message": "secret prompt"},
    )
    assert response.headers["X-Request-ID"] == "request_1234"
    assert response.headers["Cache-Control"] == "no-store"
    assert len(repository.rows) == 1
    serialized = str(repository.rows[0])
    assert "secret-key" not in serialized
    assert "secret prompt" not in serialized
    assert "secret answer" not in serialized
    assert "dfk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_secret" not in serialized
    assert "[redacted-api-key]" in serialized
    assert repository.rows[0]["client_ip_hash"]


def test_invalid_request_id_is_replaced():
    app = FastAPI()
    app.add_middleware(ExternalAuditMiddleware)

    @app.get("/api/v1/external/test")
    async def test_route():
        return {"ok": True}

    response = TestClient(app).get("/api/v1/external/test", headers={"X-Request-ID": "../unsafe"})
    assert response.headers["X-Request-ID"].startswith("req_")


def test_unhandled_error_is_sanitized_and_audited():
    app = FastAPI()
    repository = AuditRepo()
    app.state.external_audit_repo = repository
    app.add_middleware(ExternalAuditMiddleware)

    @app.get("/api/v1/external/test")
    async def test_route():
        raise RuntimeError("database password leaked")

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/external/test")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "database password leaked" not in response.text
    assert response.headers["X-Request-ID"].startswith("req_")
    assert repository.rows[0]["status_code"] == 500


def test_oversized_external_request_is_rejected_before_route():
    app = FastAPI()
    repository = AuditRepo()
    app.state.external_audit_repo = repository
    app.add_middleware(ExternalAuditMiddleware)

    @app.post("/api/v1/external/test")
    async def test_route():
        raise AssertionError("route must not execute")

    response = TestClient(app).post(
        "/api/v1/external/test",
        headers={"Content-Length": str(256 * 1024 + 1)},
        content=b"{}",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert repository.rows[0]["status_code"] == 413


def test_agent_audit_separates_owner_and_hashed_external_actor():
    class KeyRepo:
        async def verify(self, credential):
            if credential == "valid":
                return {"id": "key_1", "agent_id": "pa_1"}
            return None

        async def touch_last_used(self, key_id):
            assert key_id == "key_1"

    class AgentRepo:
        async def get_owner(self, agent_id):
            return "owner-1" if agent_id == "pa_1" else None

    app = FastAPI()
    repository = AuditRepo()
    app.state.external_audit_repo = repository
    app.state.agent_api_key_repo = KeyRepo()
    app.state.published_agent_repo = AgentRepo()
    app.add_middleware(AgentAPIAuthMiddleware)
    app.add_middleware(ExternalAuditMiddleware)

    @app.get("/api/v1/agents/{agent_id}")
    async def agent_route(agent_id: str):
        return {"agent_id": agent_id}

    response = TestClient(app).get(
        "/api/v1/agents/pa_1",
        headers={
            "Authorization": "Bearer valid",
            "User-Agent": "client dfa_aaaaaaaa_secret-value",
        },
    )
    assert response.status_code == 200
    row = repository.rows[0]
    assert row["user_id"] is None
    assert row["owner_user_id"] == "owner-1"
    assert row["agent_id"] == "pa_1"
    assert row["credential_id"] == "key_1"
    assert row["source"] == "api"
    assert row["external_actor_hash"] == hashlib.sha256(b"agent-key:key_1").hexdigest()
    assert "agent-key:key_1" not in repr(row)
    assert "dfa_aaaaaaaa_secret-value" not in row["user_agent"]
    assert "[redacted-api-key]" in row["user_agent"]
