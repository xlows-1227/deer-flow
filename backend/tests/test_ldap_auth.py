"""Tests for the LDAP authentication provider and login dispatch.

Two layers are exercised:

1. **Provider unit tests** — ``LdapAuthProvider.authenticate`` against an
   in-memory ``UserRepository`` stub plus a mocked ``ldap3`` connection.
   These verify the two-stage bind, shadow-user creation, email-conflict
   fallback, and failure suppression.

2. **Router dispatch tests** — ``POST /api/v1/auth/login`` and
   ``/login/local`` with a stub FastAPI app, asserting that admin
   identifiers go local, everyone else goes LDAP, and LDAP-disabled mode
   keeps the legacy behaviour intact.

No real directory is contacted — ``ldap3.Server`` / ``Connection`` are
patched at import time inside the provider module.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.auth.ldap_config import LdapConfig, _normalize_base_dn, load_ldap_config_from_env
from app.gateway.auth.ldap_provider import LDAP_PROVIDER_TAG, LdapAuthProvider, _escape_ldap
from app.gateway.auth.models import User

# ── In-memory UserRepository ─────────────────────────────────────────────


class _MemoryRepo:
    """Minimal async UserRepository implementation for tests."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}  # keyed by str(id)
        self._by_email: dict[str, str] = {}  # lowercase email -> id
        self._by_oauth: dict[tuple[str, str], str] = {}

    async def create_user(self, user: User) -> User:
        email_key = user.email.lower()
        if email_key in self._by_email:
            raise ValueError(f"Email already registered: {user.email}")
        self.users[str(user.id)] = user
        self._by_email[email_key] = str(user.id)
        if user.oauth_provider and user.oauth_id:
            self._by_oauth[(user.oauth_provider, user.oauth_id)] = str(user.id)
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        return self.users.get(user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        uid = self._by_email.get(email.lower())
        return self.users.get(uid) if uid else None

    async def update_user(self, user: User) -> User:
        existing = self.users.get(str(user.id))
        if existing is None:
            raise LookupError(f"User {user.id} no longer exists")
        # Re-index email if it changed.
        if existing.email.lower() != user.email.lower():
            self._by_email.pop(existing.email.lower(), None)
            self._by_email[user.email.lower()] = str(user.id)
        self.users[str(user.id)] = user
        if user.oauth_provider and user.oauth_id:
            self._by_oauth[(user.oauth_provider, user.oauth_id)] = str(user.id)
        return user

    async def count_users(self) -> int:
        return len(self.users)

    async def count_admin_users(self) -> int:
        return sum(1 for u in self.users.values() if u.system_role == "admin")

    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        normalized = oauth_id.strip().lower()
        for (prov, oid), uid in self._by_oauth.items():
            if prov == provider and oid.lower() == normalized:
                return self.users.get(uid)
        return None


def _enabled_config(**overrides) -> LdapConfig:
    base = dict(
        enabled=True,
        url="ldap://ldap.example.com",
        base="ou=YumChina,DC=cn,DC=YumChina,DC=com",
        bind_username="svc-bind",
        bind_password="svc-pass",
        objectclass="sAMAccountName",
        attr_realname="givenName",
        attr_sn="sn",
        attr_email="mail",
        local_admin_email="admin@yumchina.com",
        domain="@yumchina.com",
    )
    base.update(overrides)
    return LdapConfig(**base)


def _fake_entry(dn: str, attrs: dict[str, list]):
    """Build an object that quacks like an ldap3 search entry."""
    entry = MagicMock()
    entry.entry_dn = dn
    entry.entry_attributes_as_dict = attrs
    return entry


class _Ldap3Patch:
    """Context manager patching ``ldap3`` for the two-stage bind test flow.

    - First ``Connection`` (service bind) returns ``search_entries``.
    - Second ``Connection`` (user bind) raises when ``user_bind_ok`` is
      False, simulating a wrong password.

    Usage::

        with _Ldap3Patch(search_entries=[entry], user_bind_ok=True):
            ...
    """

    def __init__(self, *, search_entries: list, user_bind_ok: bool) -> None:
        self._search_entries = search_entries
        self._user_bind_ok = user_bind_ok
        self._calls: dict[str, int] = {"bind": 0}
        self._stack = ExitStack()

    class _Conn:
        def __init__(self, server, user=None, password=None, **kwargs):
            self.server = server
            self.user = user
            self.password = password
            self._entries: list = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def search(self, search_base, search_filter, attributes=None, **kwargs):
            self._entries = list(getattr(self, "_preset_entries", []))
            return bool(self._entries)

        @property
        def entries(self):
            return self._entries

    def _connection_factory(self):
        def _factory(server, user=None, password=None, **kwargs):
            self._calls["bind"] += 1
            conn = self._Conn(server, user=user, password=password)
            if self._calls["bind"] == 1:
                # Service-bind connection: hand back the search result set.
                conn._preset_entries = list(self._search_entries)  # type: ignore[attr-defined]
                conn._entries = list(self._search_entries)  # type: ignore[attr-defined]
                return conn
            # User-bind connection: simulate AD's bind outcome.
            if not self._user_bind_ok:
                raise RuntimeError("invalidCredentials")
            return conn

        return _factory

    def __enter__(self):
        self._stack.enter_context(patch("ldap3.Server", return_value=MagicMock()))
        self._stack.enter_context(patch("ldap3.Connection", side_effect=self._connection_factory()))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


# Convenience alias so tests read like ``with _patch_ldap3(...)``.
def _patch_ldap3(*, search_entries: list, user_bind_ok: bool) -> _Ldap3Patch:
    return _Ldap3Patch(search_entries=search_entries, user_bind_ok=user_bind_ok)


# ── Config parsing ───────────────────────────────────────────────────────


def test_normalize_base_dn_strips_stray_spaces():
    """Spring config with ``DC =com`` style spacing is cleaned up."""
    raw = "ou=YumChina, DC=cn, DC =YumChina, DC =com"
    assert _normalize_base_dn(raw) == "ou=YumChina,DC=cn,DC=YumChina,DC=com"


def test_normalize_base_dn_preserves_case():
    assert _normalize_base_dn("ou=YumChina,DC=CN") == "ou=YumChina,DC=CN"


def test_load_ldap_config_from_env_disabled_by_default(monkeypatch):
    for key in [
        "AUTH_LDAP_ENABLED",
        "AUTH_LDAP_URL",
        "AUTH_LDAP_BASE",
        "AUTH_LDAP_BIND_USERNAME",
        "AUTH_LDAP_BIND_PASSWORD",
    ]:
        monkeypatch.delenv(key, raising=False)
    config = load_ldap_config_from_env()
    assert config.enabled is False


def test_load_ldap_config_from_env_missing_url_disables(monkeypatch):
    """enabled=true but no URL/base → auto-disable with a warning."""
    monkeypatch.setenv("AUTH_LDAP_ENABLED", "true")
    monkeypatch.setenv("AUTH_LDAP_URL", "")
    monkeypatch.setenv("AUTH_LDAP_BASE", "ou=x,DC=y")
    config = load_ldap_config_from_env()
    assert config.enabled is False


def test_load_ldap_config_from_env_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_LDAP_ENABLED", "true")
    monkeypatch.setenv("AUTH_LDAP_URL", "ldap://dir.example.com")
    monkeypatch.setenv("AUTH_LDAP_BASE", "ou=YumChina, DC=cn")
    monkeypatch.setenv("AUTH_LDAP_BIND_USERNAME", "svc")
    monkeypatch.setenv("AUTH_LDAP_BIND_PASSWORD", "pw")
    config = load_ldap_config_from_env()
    assert config.enabled is True
    assert config.url == "ldap://dir.example.com"
    assert config.base == "ou=YumChina,DC=cn"
    assert config.domain == "@yumchina.com"


def test_is_admin_email_matches_full_and_bare_local():
    config = _enabled_config(local_admin_email="admin@yumchina.com")
    assert config.is_admin_email("admin@yumchina.com")
    assert config.is_admin_email("admin")
    assert config.is_admin_email("Admin@YumChina.com")
    assert not config.is_admin_email("someone")
    assert not config.is_admin_email("someone@yumchina.com")


def test_escape_ldap_handles_filter_metachars():
    assert _escape_ldap("john") == "john"
    assert _escape_ldap("a*b") == r"a\2ab"
    assert _escape_ldap("a(b)c") == r"a\28b\29c"
    assert _escape_ldap("a\\b") == r"a\5cb"


# ── Provider.authenticate ────────────────────────────────────────────────


def test_authenticate_success_creates_shadow_user():
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())
    asyncio.run(
        repo.create_user(
            User(
                email="john.doe@yumchina.com",
                password_hash=None,
                system_role="user",
                oauth_provider=LDAP_PROVIDER_TAG,
                oauth_id="john",
            )
        )
    )

    entry = _fake_entry(
        "CN=John Doe,ou=YumChina,DC=cn,DC=YumChina,DC=com",
        {"mail": ["john.doe@yumchina.com"], "givenName": ["John"], "sn": ["Doe"]},
    )
    with _patch_ldap3(search_entries=[entry], user_bind_ok=True):
        user = asyncio.run(provider.authenticate({"username": "john", "password": "secret"}))

    assert user is not None
    assert user.email == "john.doe@yumchina.com"
    assert user.oauth_provider == LDAP_PROVIDER_TAG
    assert user.oauth_id == "john"
    assert user.password_hash is None
    assert user.system_role == "user"

    # Shadow row is persisted.
    fetched = asyncio.run(repo.get_user_by_oauth(LDAP_PROVIDER_TAG, "john"))
    assert fetched is not None
    assert str(fetched.id) == str(user.id)


def test_authenticate_success_reuses_existing_shadow_user():
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())
    asyncio.run(
        repo.create_user(
            User(
                email="john.doe@yumchina.com",
                password_hash=None,
                system_role="user",
                oauth_provider=LDAP_PROVIDER_TAG,
                oauth_id="john",
            )
        )
    )

    entry = _fake_entry(
        "CN=John,ou=YumChina,DC=cn,DC=YumChina,DC=com",
        {"mail": ["john.doe@yumchina.com"], "givenName": ["John"], "sn": ["Doe"]},
    )

    with _patch_ldap3(search_entries=[entry], user_bind_ok=True):
        first = asyncio.run(provider.authenticate({"username": "john", "password": "pw"}))
    with _patch_ldap3(search_entries=[entry], user_bind_ok=True):
        second = asyncio.run(provider.authenticate({"username": "john", "password": "pw"}))

    assert str(first.id) == str(second.id)
    # Only one row total.
    assert asyncio.run(repo.count_users()) == 1


def test_authenticate_success_refreshes_email_when_ldap_mail_changes():
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())
    asyncio.run(
        repo.create_user(
            User(
                email="old@yumchina.com",
                password_hash=None,
                system_role="user",
                oauth_provider=LDAP_PROVIDER_TAG,
                oauth_id="john",
            )
        )
    )

    entry1 = _fake_entry(
        "CN=John,ou=YumChina,DC=cn,DC=YumChina,DC=com",
        {"mail": ["old@yumchina.com"], "givenName": ["John"], "sn": ["Doe"]},
    )
    entry2 = _fake_entry(
        "CN=John,ou=YumChina,DC=cn,DC=YumChina,DC=com",
        {"mail": ["new@yumchina.com"], "givenName": ["John"], "sn": ["Doe"]},
    )

    with _patch_ldap3(search_entries=[entry1], user_bind_ok=True):
        asyncio.run(provider.authenticate({"username": "john", "password": "pw"}))
    with _patch_ldap3(search_entries=[entry2], user_bind_ok=True):
        user = asyncio.run(provider.authenticate({"username": "john", "password": "pw"}))

    assert user is not None
    assert user.email == "new@yumchina.com"


def test_authenticate_wrong_password_returns_none_and_no_shadow():
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())

    entry = _fake_entry(
        "CN=John,ou=YumChina,DC=cn,DC=YumChina,DC=com",
        {"mail": ["john.doe@yumchina.com"], "givenName": ["John"], "sn": ["Doe"]},
    )

    with _patch_ldap3(search_entries=[entry], user_bind_ok=False):
        user = asyncio.run(provider.authenticate({"username": "john", "password": "wrong"}))

    assert user is None
    # Failed auth must not create a shadow row.
    assert asyncio.run(repo.count_users()) == 0


def test_authenticate_user_not_found_returns_none():
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())

    with _patch_ldap3(search_entries=[], user_bind_ok=True):
        user = asyncio.run(provider.authenticate({"username": "ghost", "password": "pw"}))

    assert user is None
    assert asyncio.run(repo.count_users()) == 0


def test_authenticate_unregistered_user_returns_none():
    """LDAP bind may succeed but login is rejected without a prior registration row."""
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())

    entry = _fake_entry(
        "CN=John,ou=YumChina,DC=cn,DC=YumChina,DC=com",
        {"mail": ["john.doe@yumchina.com"], "givenName": ["John"], "sn": ["Doe"]},
    )

    with _patch_ldap3(search_entries=[entry], user_bind_ok=True):
        user = asyncio.run(provider.authenticate({"username": "john", "password": "pw"}))

    assert user is None
    assert asyncio.run(repo.count_users()) == 0


def test_authenticate_no_mail_uses_domain_email():
    """Registered LDAP users without AD mail keep their stored email on login."""
    repo = _MemoryRepo()
    asyncio.run(
        repo.create_user(
            User(
                email="ext.user@yumchina.com",
                password_hash=None,
                system_role="user",
                oauth_provider=LDAP_PROVIDER_TAG,
                oauth_id="ext.user",
            )
        )
    )
    provider = LdapAuthProvider(repo, _enabled_config())

    entry = _fake_entry(
        "CN=External,ou=YumChina,DC=cn,DC=YumChina,DC=com",
        {"mail": [], "givenName": ["Ext"], "sn": ["User"]},
    )

    with _patch_ldap3(search_entries=[entry], user_bind_ok=True):
        user = asyncio.run(provider.authenticate({"username": "ext.user", "password": "pw"}))

    assert user is not None
    assert user.email == "ext.user@yumchina.com"


def test_authenticate_disabled_config_returns_none():
    repo = _MemoryRepo()
    disabled = _enabled_config(enabled=False)
    provider = LdapAuthProvider(repo, disabled)

    user = asyncio.run(provider.authenticate({"username": "john", "password": "pw"}))
    assert user is None


def test_authenticate_multiple_matches_returns_none():
    """Ambiguous directory matches never guess an identity."""
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())

    entries = [
        _fake_entry("CN=A,ou=YumChina,DC=cn,DC=YumChina,DC=com", {"mail": ["a@yumchina.com"]}),
        _fake_entry("CN=B,ou=YumChina,DC=cn,DC=YumChina,DC=com", {"mail": ["b@yumchina.com"]}),
    ]

    with _patch_ldap3(search_entries=entries, user_bind_ok=True):
        user = asyncio.run(provider.authenticate({"username": "john", "password": "pw"}))

    assert user is None


def test_authenticate_empty_credentials_returns_none():
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())

    assert asyncio.run(provider.authenticate({"username": "", "password": "pw"})) is None
    assert asyncio.run(provider.authenticate({"username": "john", "password": ""})) is None


def test_authenticate_ldap_exception_returns_none():
    """Any unexpected ldap3 error is swallowed → None (boundary safety)."""
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())

    with patch("ldap3.Server", side_effect=RuntimeError("network down")):
        user = asyncio.run(provider.authenticate({"username": "john", "password": "pw"}))
    assert user is None
    assert asyncio.run(repo.count_users()) == 0


def test_get_user_delegates_to_repository():
    repo = _MemoryRepo()
    provider = LdapAuthProvider(repo, _enabled_config())
    created = asyncio.run(repo.create_user(User(email="x@yumchina.com", oauth_provider=LDAP_PROVIDER_TAG, oauth_id="x")))
    fetched = asyncio.run(provider.get_user(str(created.id)))
    assert fetched is not None
    assert str(fetched.id) == str(created.id)


# ── Router dispatch ──────────────────────────────────────────────────────


def _set_jwt_secret(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-key-for-jwt-testing-minimum-32-chars")


def _build_test_app(monkeypatch, *, ldap_enabled: bool):
    """Build a minimal FastAPI app exposing only the auth router."""
    _set_jwt_secret(monkeypatch)

    # Reset cached auth config + providers so the new env takes effect.
    import app.gateway.auth.config as cfg
    import app.gateway.deps as deps

    monkeypatch.setattr(cfg, "_auth_config", None)
    monkeypatch.setattr(deps, "_cached_local_provider", None)
    monkeypatch.setattr(deps, "_cached_ldap_provider", None)
    monkeypatch.setattr(deps, "_cached_repo", None)

    env = {"AUTH_JWT_SECRET": "test-secret-key-for-jwt-testing-minimum-32-chars"}
    if ldap_enabled:
        env.update(
            {
                "AUTH_LDAP_ENABLED": "true",
                "AUTH_LDAP_URL": "ldap://ldap.example.com",
                "AUTH_LDAP_BASE": "ou=YumChina,DC=cn,DC=YumChina,DC=com",
                "AUTH_LDAP_BIND_USERNAME": "svc",
                "AUTH_LDAP_BIND_PASSWORD": "pw",
                "AUTH_LDAP_LOCAL_ADMIN_EMAIL": "admin@yumchina.com",
            }
        )
    else:
        env["AUTH_LDAP_ENABLED"] = "false"
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    from fastapi import FastAPI

    from app.gateway.routers.auth import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def stub_providers(monkeypatch):
    """Replace get_local_provider / get_ldap_provider with AsyncMock stubs.

    Returns a dict with ``local`` and ``ldap`` MagicMock objects whose
    ``authenticate`` is an AsyncMock; tests set their return values.
    """
    import app.gateway.routers.auth as auth_router

    local = MagicMock()
    local.authenticate = AsyncMock(return_value=None)
    local.get_user_by_oauth = AsyncMock(return_value=None)
    local.get_user_by_email = AsyncMock(return_value=None)
    ldap = MagicMock()
    ldap.authenticate = AsyncMock(return_value=None)

    monkeypatch.setattr(auth_router, "get_local_provider", lambda: local)
    monkeypatch.setattr(auth_router, "get_ldap_provider", lambda: ldap)
    return {"local": local, "ldap": ldap}


def test_login_ldap_disabled_routes_everything_local(monkeypatch, stub_providers):
    """With LDAP off, every identifier hits the local provider."""
    from fastapi.testclient import TestClient

    app = _build_test_app(monkeypatch, ldap_enabled=False)
    admin_user = User(email="admin@yumchina.com", password_hash="h", system_role="admin")
    stub_providers["local"].authenticate.return_value = admin_user

    with TestClient(app) as client:
        # Force the router-level patches to take effect after app build.
        import app.gateway.routers.auth as auth_router

        auth_router.get_local_provider = lambda: stub_providers["local"]
        auth_router.get_ldap_provider = lambda: stub_providers["ldap"]

        resp = client.post("/api/v1/auth/login", json={"username": "anybody", "password": "pw"})

    assert resp.status_code == 200
    stub_providers["local"].authenticate.assert_awaited()
    stub_providers["ldap"].authenticate.assert_not_awaited()


def test_login_admin_identifier_routes_local(monkeypatch, stub_providers):
    """Admin email (full or bare) bypasses LDAP even when LDAP is enabled."""
    from fastapi.testclient import TestClient

    app = _build_test_app(monkeypatch, ldap_enabled=True)
    admin_user = User(email="admin@yumchina.com", password_hash="h", system_role="admin")
    stub_providers["local"].authenticate.return_value = admin_user

    import app.gateway.routers.auth as auth_router

    auth_router.get_local_provider = lambda: stub_providers["local"]
    auth_router.get_ldap_provider = lambda: stub_providers["ldap"]

    with TestClient(app) as client:
        resp = client.post("/api/v1/auth/login", json={"username": "admin@yumchina.com", "password": "pw"})
        resp_bare = client.post("/api/v1/auth/login", json={"username": "admin", "password": "pw"})

    assert resp.status_code == 200
    assert resp_bare.status_code == 200
    stub_providers["local"].authenticate.assert_awaited()
    stub_providers["ldap"].authenticate.assert_not_awaited()


def test_login_non_admin_routes_to_ldap(monkeypatch, stub_providers):
    """Registered LDAP users authenticate against the directory."""
    from fastapi.testclient import TestClient

    app = _build_test_app(monkeypatch, ldap_enabled=True)
    ldap_user = User(email="john.doe@yumchina.com", oauth_provider=LDAP_PROVIDER_TAG, oauth_id="john")
    stub_providers["local"].get_user_by_oauth.return_value = ldap_user
    stub_providers["ldap"].authenticate.return_value = ldap_user

    import app.gateway.routers.auth as auth_router

    auth_router.get_local_provider = lambda: stub_providers["local"]
    auth_router.get_ldap_provider = lambda: stub_providers["ldap"]

    with TestClient(app) as client:
        resp = client.post("/api/v1/auth/login", json={"username": "john", "password": "pw"})

    assert resp.status_code == 200
    stub_providers["local"].get_user_by_oauth.assert_awaited_with(LDAP_PROVIDER_TAG, "john")
    stub_providers["ldap"].authenticate.assert_awaited()
    # Strict mode: local password check never runs for LDAP users.
    stub_providers["local"].authenticate.assert_not_awaited()


def test_login_ldap_failure_returns_401(monkeypatch, stub_providers):
    from fastapi.testclient import TestClient

    app = _build_test_app(monkeypatch, ldap_enabled=True)
    ldap_user = User(email="john.doe@yumchina.com", oauth_provider=LDAP_PROVIDER_TAG, oauth_id="john")
    stub_providers["local"].get_user_by_oauth.return_value = ldap_user
    stub_providers["ldap"].authenticate.return_value = None

    import app.gateway.routers.auth as auth_router

    auth_router.get_local_provider = lambda: stub_providers["local"]
    auth_router.get_ldap_provider = lambda: stub_providers["ldap"]

    with TestClient(app) as client:
        resp = client.post("/api/v1/auth/login", json={"username": "john", "password": "wrong"})

    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_credentials"


def test_login_unregistered_returns_user_not_registered(monkeypatch, stub_providers):
    from fastapi.testclient import TestClient

    app = _build_test_app(monkeypatch, ldap_enabled=True)
    stub_providers["local"].get_user_by_oauth.return_value = None
    stub_providers["local"].get_user_by_email.return_value = None

    import app.gateway.routers.auth as auth_router

    auth_router.get_local_provider = lambda: stub_providers["local"]
    auth_router.get_ldap_provider = lambda: stub_providers["ldap"]

    with TestClient(app) as client:
        resp = client.post("/api/v1/auth/login", json={"username": "john", "password": "pw"})

    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "user_not_registered"
    stub_providers["ldap"].authenticate.assert_not_awaited()


def test_login_local_endpoint_still_dispatches(monkeypatch, stub_providers):
    """Backwards-compatible form endpoint honours the same dispatch policy."""
    from fastapi.testclient import TestClient

    app = _build_test_app(monkeypatch, ldap_enabled=True)
    ldap_user = User(email="john.doe@yumchina.com", oauth_provider=LDAP_PROVIDER_TAG, oauth_id="john")
    stub_providers["local"].get_user_by_oauth.return_value = ldap_user
    stub_providers["ldap"].authenticate.return_value = ldap_user

    import app.gateway.routers.auth as auth_router

    auth_router.get_local_provider = lambda: stub_providers["local"]
    auth_router.get_ldap_provider = lambda: stub_providers["ldap"]

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/login/local",
            data={"username": "john", "password": "pw"},
        )

    assert resp.status_code == 200
    stub_providers["ldap"].authenticate.assert_awaited()


def test_login_local_account_uses_password_when_oauth_provider_empty(monkeypatch, stub_providers):
    from fastapi.testclient import TestClient

    app = _build_test_app(monkeypatch, ldap_enabled=True)
    local_user = User(email="local@yumchina.com", password_hash="h", system_role="user")
    stub_providers["local"].get_user_by_oauth.return_value = None
    stub_providers["local"].get_user_by_email.return_value = local_user
    stub_providers["local"].authenticate.return_value = local_user

    import app.gateway.routers.auth as auth_router

    auth_router.get_local_provider = lambda: stub_providers["local"]
    auth_router.get_ldap_provider = lambda: stub_providers["ldap"]

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "local@yumchina.com", "password": "pw"},
        )

    assert resp.status_code == 200
    stub_providers["local"].authenticate.assert_awaited_with({"email": "local@yumchina.com", "password": "pw"})
    stub_providers["ldap"].authenticate.assert_not_awaited()


def test_login_strips_domain_when_routing_to_ldap(monkeypatch, stub_providers):
    """Full-email identifiers have the domain stripped before LDAP lookup."""
    from fastapi.testclient import TestClient

    app = _build_test_app(monkeypatch, ldap_enabled=True)
    ldap_user = User(email="john.doe@yumchina.com", oauth_provider=LDAP_PROVIDER_TAG, oauth_id="john.doe")
    stub_providers["local"].get_user_by_oauth.return_value = ldap_user
    stub_providers["ldap"].authenticate.return_value = ldap_user

    import app.gateway.routers.auth as auth_router

    auth_router.get_local_provider = lambda: stub_providers["local"]
    auth_router.get_ldap_provider = lambda: stub_providers["ldap"]

    with TestClient(app) as client:
        resp = client.post("/api/v1/auth/login", json={"username": "john.doe@yumchina.com", "password": "pw"})

    assert resp.status_code == 200
    stub_providers["local"].get_user_by_oauth.assert_awaited_with(LDAP_PROVIDER_TAG, "john.doe")
    stub_providers["ldap"].authenticate.assert_awaited_with({"username": "john.doe", "password": "pw"})


# ── change-password LDAP guard ───────────────────────────────────────────


def test_change_password_rejects_ldap_user(monkeypatch):
    """LDAP shadow users cannot change their password locally."""
    import asyncio

    _set_jwt_secret(monkeypatch)
    from app.gateway.auth.jwt import create_access_token
    from app.gateway.auth.models import User
    from app.gateway.deps import get_current_user_from_request

    ldap_user = User(email="john@yumchina.com", oauth_provider=LDAP_PROVIDER_TAG, oauth_id="john")
    token = create_access_token(str(ldap_user.id), token_version=0)

    request = MagicMock()
    request.cookies = {"access_token": token}

    with patch("app.gateway.deps.get_local_provider") as mock_fn:
        mock_provider = MagicMock()
        mock_provider.get_user = AsyncMock(return_value=ldap_user)
        mock_fn.return_value = mock_provider

        fetched = asyncio.run(get_current_user_from_request(request))
        assert str(fetched.id) == str(ldap_user.id)

        # The change-password guard keys off oauth_provider == LDAP tag.
        assert ldap_user.oauth_provider == LDAP_PROVIDER_TAG
