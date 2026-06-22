"""Tests for CSRF middleware."""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.gateway.csrf_middleware import CSRFMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)

    @app.post("/api/v1/auth/login/local")
    async def login_local():
        return {"ok": True}

    @app.post("/api/v1/auth/register")
    async def register():
        return {"ok": True}

    @app.post("/api/threads/abc/runs/stream")
    async def protected_mutation():
        return {"ok": True}

    @app.get("/api/skills")
    async def list_skills():
        return {"skills": []}

    @app.get("/api/models")
    async def list_models():
        return {"models": []}

    return app


def test_auth_post_rejects_cross_origin_browser_request():
    """CSRF-exempt auth routes must not accept hostile browser origins.

    Login/register endpoints intentionally skip the double-submit token because
    first-time callers do not have a token yet. They still set an auth session,
    so a hostile cross-site form POST must be rejected to avoid login CSRF /
    session fixation.
    """
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-site auth request denied."


def test_auth_post_allows_same_origin_browser_request():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={"Origin": "https://deerflow.example"},
    )

    assert response.status_code == 200
    assert response.cookies.get("csrf_token")


def test_auth_post_rejects_malformed_origin_with_path():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={"Origin": "https://deerflow.example/path"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-site auth request denied."
    assert response.cookies.get("csrf_token") is None


def test_auth_post_rejects_malformed_origin_with_invalid_port():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={"Origin": "https://deerflow.example:bad"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-site auth request denied."
    assert response.cookies.get("csrf_token") is None


def test_auth_post_allows_same_origin_default_port_equivalence():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={"Origin": "https://deerflow.example:443"},
    )

    assert response.status_code == 200
    assert response.cookies.get("csrf_token")


def test_auth_post_allows_forwarded_same_origin():
    client = TestClient(_make_app(), base_url="http://internal:8000")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={
            "Origin": "https://deerflow.example",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "deerflow.example, internal:8000",
        },
    )

    assert response.status_code == 200
    assert response.cookies.get("csrf_token")


def test_auth_post_allows_forwarded_same_origin_with_non_default_port():
    client = TestClient(_make_app(), base_url="http://internal:8000")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={
            "Origin": "http://localhost:2026",
            "X-Forwarded-Proto": "http",
            "X-Forwarded-Host": "localhost:2026",
        },
    )

    assert response.status_code == 200
    assert response.cookies.get("csrf_token")


def test_auth_post_allows_rfc_forwarded_same_origin():
    client = TestClient(_make_app(), base_url="http://internal:8000")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={
            "Origin": "https://deerflow.example",
            "Forwarded": "proto=https;host=deerflow.example",
        },
    )

    assert response.status_code == 200
    assert response.cookies.get("csrf_token")
    assert "secure" in response.headers["set-cookie"].lower()


def test_auth_post_allows_explicit_configured_origin(monkeypatch):
    monkeypatch.setenv("GATEWAY_CORS_ORIGINS", "https://app.example")
    client = TestClient(_make_app(), base_url="https://api.example")

    response = client.post(
        "/api/v1/auth/register",
        headers={"Origin": "https://app.example"},
    )

    assert response.status_code == 200
    assert response.cookies.get("csrf_token")


def test_auth_post_does_not_treat_wildcard_cors_as_allowed_origin(monkeypatch):
    monkeypatch.setenv("GATEWAY_CORS_ORIGINS", "*")
    client = TestClient(_make_app(), base_url="https://api.example")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-site auth request denied."


def test_auth_post_sets_strict_samesite_csrf_cookie():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={"Origin": "https://deerflow.example"},
    )

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"].lower()
    assert "csrf_token=" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "secure" in set_cookie


def test_auth_post_without_origin_still_allows_non_browser_clients():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.post("/api/v1/auth/login/local")

    assert response.status_code == 200
    assert response.cookies.get("csrf_token")


def test_non_auth_mutation_still_requires_double_submit_token():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.post(
        "/api/threads/abc/runs/stream",
        headers={"Origin": "https://deerflow.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token missing. Include X-CSRF-Token header."


def test_non_auth_mutation_allows_valid_double_submit_token():
    client = TestClient(_make_app(), base_url="https://deerflow.example")
    client.cookies.set("csrf_token", "known-token")

    response = client.post(
        "/api/threads/abc/runs/stream",
        headers={
            "Origin": "https://deerflow.example",
            "X-CSRF-Token": "known-token",
        },
    )

    assert response.status_code == 200


def test_non_auth_mutation_rejects_mismatched_double_submit_token():
    client = TestClient(_make_app(), base_url="https://deerflow.example")
    client.cookies.set("csrf_token", "cookie-token")

    response = client.post(
        "/api/threads/abc/runs/stream",
        headers={
            "Origin": "https://deerflow.example",
            "X-CSRF-Token": "header-token",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token mismatch."


def test_skills_list_allows_request_without_csrf_header():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.get("/api/skills")

    assert response.status_code == 200
    assert response.json() == {"skills": []}


def test_skills_list_rejects_invalid_csrf_header():
    client = TestClient(_make_app(), base_url="https://deerflow.example")
    client.cookies.set("csrf_token", "known-token")

    response = client.get(
        "/api/skills",
        headers={"X-CSRF-Token": "1"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token mismatch."


def test_skills_list_allows_valid_double_submit_token():
    client = TestClient(_make_app(), base_url="https://deerflow.example")
    client.cookies.set("csrf_token", "known-token")

    response = client.get(
        "/api/skills",
        headers={"X-CSRF-Token": "known-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"skills": []}


def test_other_read_endpoints_allow_request_without_csrf_header():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.get("/api/models")

    assert response.status_code == 200


def test_other_read_endpoints_reject_invalid_csrf_header():
    client = TestClient(_make_app(), base_url="https://deerflow.example")
    client.cookies.set("csrf_token", "known-token")

    response = client.get(
        "/api/models",
        headers={"X-CSRF-Token": "forged-token"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token mismatch."


def test_read_endpoint_rejects_csrf_header_without_cookie():
    client = TestClient(_make_app(), base_url="https://deerflow.example")

    response = client.get(
        "/api/models",
        headers={"X-CSRF-Token": "orphan-token"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token missing. Include X-CSRF-Token header."
