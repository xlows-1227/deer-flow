from fastapi import FastAPI
from starlette.testclient import TestClient

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
