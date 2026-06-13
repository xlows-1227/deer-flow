
"""Tests for CSRF middleware."""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.gateway import csrf_middleware as csrf_mod
from app.gateway.csrf_middleware import CSRFMiddleware


@pytest.fixture
def trust_testclient(monkeypatch):
    """Temporarily treat Starlette's TestClient peer as a trusted proxy."""
    original = csrf_mod._is_trusted_proxy

    def _patched(host: str | None) -> bool:
        return host == "testclient" or original(host)

    monkeypatch.setattr(csrf_mod, "_is_trusted_proxy", _patched)
    yield


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


@pytest.mark.usefixtures("trust_testclient")
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


@pytest.mark.usefixtures("trust_testclient")
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


@pytest.mark.usefixtures("trust_testclient")
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


def test_auth_post_rejects_forwarded_header_from_untrusted_proxy():
    """A private-network client must not be able to forge forwarded headers."""
    client = TestClient(_make_app(), base_url="http://internal:8000")

    response = client.post(
        "/api/v1/auth/login/local",
        headers={
            "Origin": "https://deerflow.example",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "deerflow.example",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-site auth request denied."


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


def test_invalid_trusted_proxy_config_is_skipped_with_warning(monkeypatch, caplog):
    """Garbage entries in GATEWAY_TRUSTED_PROXIES are skipped and logged."""
    import logging

    monkeypatch.setenv("GATEWAY_TRUSTED_PROXIES", "not-an-ip,127.0.0.1,::1")
    # Force re-parsing with the new env value.
    monkeypatch.setattr(
        csrf_mod,
        "_TRUSTED_PROXY_NETWORKS",
        csrf_mod._load_trusted_proxy_networks(),
    )

    with caplog.at_level(logging.WARNING):
        networks = csrf_mod._TRUSTED_PROXY_NETWORKS

    assert any("not-an-ip" in msg for msg in caplog.messages)
    assert len(networks) == 2
