"""Tests for admin user management: list / reset password / soft delete.

Covers the repository layer (logical delete + filtered listings), the
admin router (auth boundary, self-delete guard, configured reset
password), login rejection for deleted accounts, JWT rejection through
``get_current_user_from_request``, and skill-share hydration hiding
deleted users.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.gateway import deps
from app.gateway.auth.config import get_auth_config
from app.gateway.auth.jwt import create_access_token
from app.gateway.deps import get_current_user_from_request, get_local_provider
from app.gateway.routers import admin_users
from app.gateway.routers.skills import _build_user_email_index, _fetch_custom_skill_sharees_and_owner
from deerflow.persistence.base import Base
from deerflow.persistence.skill_share.store import SkillShareRepository

DEFAULT_RESET_PASSWORD = "DeerFlow@2026"
ADMIN_PASSWORD = "admin-pass-123"
ALICE_PASSWORD = "alice-pass-123"
BOB_PASSWORD = "bob-pass-123"


@pytest_asyncio.fixture
async def user_env(monkeypatch: pytest.MonkeyPatch):
    """In-memory users DB wired into deps.get_local_provider()."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr("deerflow.persistence.engine.get_session_factory", lambda: session_factory)
    monkeypatch.setattr(deps, "_cached_repo", None)
    monkeypatch.setattr(deps, "_cached_local_provider", None)
    monkeypatch.setattr(deps, "_cached_ldap_provider", None)
    monkeypatch.setattr(deps, "_cached_session_factory_id", None)
    # Deterministic reset-password config regardless of the host env.
    monkeypatch.delenv("AUTH_USER_RESET_PASSWORD", raising=False)
    monkeypatch.setattr("app.gateway.auth.config._auth_config", None)
    assert get_auth_config().user_reset_password == DEFAULT_RESET_PASSWORD

    provider = get_local_provider()
    admin = await provider.create_user("admin@example.com", ADMIN_PASSWORD, system_role="admin")
    alice = await provider.create_user("alice@example.com", ALICE_PASSWORD)
    bob = await provider.create_user("bob@example.com", BOB_PASSWORD)

    yield type("Env", (), {"provider": provider, "admin": admin, "alice": alice, "bob": bob})()

    await engine.dispose()


def _client_as(user) -> TestClient:
    app = FastAPI()
    app.include_router(admin_users.router)
    app.dependency_overrides[get_current_user_from_request] = lambda: user
    return TestClient(app)


def _request_with_token(token: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", f"access_token={token}".encode("latin-1"))],
        "query_string": b"",
    }
    return Request(scope)


# ── Repository layer ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_delete_marks_row_and_revokes_token_version(user_env) -> None:
    repo = user_env.provider.repository
    before = await repo.get_user_by_id(str(user_env.alice.id))
    assert before is not None and not before.deleted

    deleted = await repo.soft_delete_user(str(user_env.alice.id))
    assert deleted is not None
    assert deleted.deleted is True
    assert deleted.deleted_at is not None
    assert deleted.token_version == before.token_version + 1

    # Row still exists (logical delete) and get_by_* still resolves it so
    # login paths can distinguish "deleted" from "unknown account".
    row = await repo.get_user_by_email("alice@example.com")
    assert row is not None and row.deleted is True


@pytest.mark.asyncio
async def test_list_users_and_counts_exclude_deleted(user_env) -> None:
    repo = user_env.provider.repository
    await repo.soft_delete_user(str(user_env.alice.id))

    emails = [str(u.email) for u in await repo.list_users()]
    assert emails == ["admin@example.com", "bob@example.com"]
    assert await repo.count_users() == 2
    assert await repo.count_admin_users() == 1

    # Deleting the last admin makes count_admin_users() zero, which
    # re-enables the /setup first-admin bootstrap as a recovery path.
    await repo.soft_delete_user(str(user_env.admin.id))
    assert await repo.count_admin_users() == 0


# ── Login / token rejection ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticate_rejects_deleted_user(user_env) -> None:
    await user_env.provider.repository.soft_delete_user(str(user_env.alice.id))
    assert await user_env.provider.authenticate({"email": "alice@example.com", "password": ALICE_PASSWORD}) is None
    assert await user_env.provider.authenticate({"email": "alice@example.com", "password": "wrong"}) is None
    # Active accounts still authenticate normally.
    bob = await user_env.provider.authenticate({"email": "bob@example.com", "password": BOB_PASSWORD})
    assert bob is not None and str(bob.email) == "bob@example.com"


@pytest.mark.asyncio
async def test_deleted_user_token_rejected_by_deps(user_env) -> None:
    token = create_access_token(str(user_env.alice.id), token_version=user_env.alice.token_version)
    user = await get_current_user_from_request(_request_with_token(token))
    assert str(user.id) == str(user_env.alice.id)

    await user_env.provider.repository.soft_delete_user(str(user_env.alice.id))
    with pytest.raises(Exception) as exc_info:
        await get_current_user_from_request(_request_with_token(token))
    assert getattr(exc_info.value, "status_code", None) == 401


# ── Router layer ──────────────────────────────────────────────────────────


def test_admin_endpoints_reject_non_admin(user_env) -> None:
    client = _client_as(user_env.bob)
    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/admin/users/reset-password-config").status_code == 403
    assert client.post(f"/api/admin/users/{str(user_env.alice.id)}/reset-password").status_code == 403
    assert client.delete(f"/api/admin/users/{str(user_env.alice.id)}").status_code == 403


def test_admin_list_returns_active_accounts_only(user_env) -> None:
    client = _client_as(user_env.admin)
    body = client.get("/api/admin/users").json()
    assert [(u["email"], u["system_role"]) for u in body["users"]] == [
        ("admin@example.com", "admin"),
        ("alice@example.com", "user"),
        ("bob@example.com", "user"),
    ]

    with client:
        assert client.delete(f"/api/admin/users/{str(user_env.alice.id)}").status_code == 200
    body = client.get("/api/admin/users").json()
    assert [u["email"] for u in body["users"]] == ["admin@example.com", "bob@example.com"]


def test_reset_password_config_returns_env_value(user_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_USER_RESET_PASSWORD", "CustomReset@99")
    monkeypatch.setattr("app.gateway.auth.config._auth_config", None)
    client = _client_as(user_env.admin)
    assert client.get("/api/admin/users/reset-password-config").json() == {"value": "CustomReset@99"}


@pytest.mark.asyncio
async def test_reset_password_applies_configured_value_and_revokes_sessions(user_env) -> None:
    client = _client_as(user_env.admin)
    old_token = create_access_token(str(user_env.alice.id), token_version=user_env.alice.token_version)

    response = client.post(f"/api/admin/users/{str(user_env.alice.id)}/reset-password")
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"

    # The configured value now authenticates; the old password does not.
    assert await user_env.provider.authenticate({"email": "alice@example.com", "password": DEFAULT_RESET_PASSWORD}) is not None
    assert await user_env.provider.authenticate({"email": "alice@example.com", "password": ALICE_PASSWORD}) is None
    # token_version was bumped → the pre-reset JWT is stale.
    with pytest.raises(Exception) as exc_info:
        await get_current_user_from_request(_request_with_token(old_token))
    assert getattr(exc_info.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_delete_is_logical_and_self_delete_forbidden(user_env) -> None:
    client = _client_as(user_env.admin)

    # Self-delete is rejected so an admin cannot lock themselves out.
    assert client.delete(f"/api/admin/users/{str(user_env.admin.id)}").status_code == 400

    response = client.delete(f"/api/admin/users/{str(user_env.bob.id)}")
    assert response.status_code == 200
    assert response.json() == {"id": str(user_env.bob.id), "email": "bob@example.com"}

    # Logical delete: row survives with deleted=True.
    row = await user_env.provider.repository.get_user_by_id(str(user_env.bob.id))
    assert row is not None and row.deleted is True

    # Deleted account: repeat delete → 404, login blocked.
    assert client.delete(f"/api/admin/users/{str(user_env.bob.id)}").status_code == 404
    assert client.post(f"/api/admin/users/{str(user_env.bob.id)}/reset-password").status_code == 404
    assert await user_env.provider.authenticate({"email": "bob@example.com", "password": BOB_PASSWORD}) is None


def test_reset_password_unknown_user_404(user_env) -> None:
    client = _client_as(user_env.admin)
    assert client.post(f"/api/admin/users/{uuid4()}/reset-password").status_code == 404


# ── Skill-share hydration hides deleted users ─────────────────────────────


@pytest.mark.asyncio
async def test_skill_share_hydration_hides_deleted_users(user_env) -> None:
    share_repo = SkillShareRepository(
        # Reuse the same in-memory engine backing get_local_provider().
        __import__("deerflow.persistence.engine", fromlist=["get_session_factory"]).get_session_factory()
    )
    await share_repo.replace_sharees(
        skill_name="bob-skill",
        owner_user_id=str(user_env.bob.id),
        sharee_user_ids={str(user_env.alice.id)},
    )
    await share_repo.replace_sharees(
        skill_name="alice-skill",
        owner_user_id=str(user_env.alice.id),
        sharee_user_ids={str(user_env.admin.id)},
    )
    await user_env.provider.repository.soft_delete_user(str(user_env.alice.id))

    # Active-user index no longer resolves the deleted id.
    index = await _build_user_email_index({str(user_env.alice.id), str(user_env.bob.id), str(user_env.admin.id)})
    assert str(user_env.bob.id).lower() in index
    assert str(user_env.admin.id).lower() in index
    assert str(user_env.alice.id).lower() not in index

    sharees_by_skill, owner_email_by_owner_id = await _fetch_custom_skill_sharees_and_owner(
        share_repo,
        skill_names=["bob-skill", "alice-skill"],
    )
    # Deleted sharee (alice on bob-skill) is dropped entirely.
    assert sharees_by_skill.get("bob-skill", []) == []
    # Active sharee still hydrates with email.
    assert [s.email for s in sharees_by_skill.get("alice-skill", [])] == ["admin@example.com"]
    # Deleted owner (alice) no longer resolves to an email.
    assert str(user_env.bob.id).lower() in owner_email_by_owner_id
    assert str(user_env.alice.id).lower() not in owner_email_by_owner_id


# ── Skill ownership transfer on delete ────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_share_repo_transfer_ownership_moves_rows(user_env) -> None:
    """SkillShareRepository.transfer_ownership reassigns grant rows."""
    share_repo = SkillShareRepository(
        __import__("deerflow.persistence.engine", fromlist=["get_session_factory"]).get_session_factory()
    )
    await share_repo.replace_sharees(
        skill_name="alice-skill",
        owner_user_id=str(user_env.alice.id),
        sharee_user_ids={str(user_env.bob.id)},
    )
    moved = await share_repo.transfer_ownership(
        from_user_id=str(user_env.alice.id),
        to_user_id=str(user_env.admin.id),
    )
    assert moved == 1
    rows = await share_repo.list_sharees_for_skill("alice-skill")
    assert len(rows) == 1
    assert rows[0].owner_user_id == str(user_env.admin.id)
    assert rows[0].shared_with_user_id == str(user_env.bob.id)


@pytest.mark.asyncio
async def test_transfer_custom_skill_ownership_rewrites_owner_files(user_env, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_transfer_custom_skill_ownership rewrites on-disk .owners/<name>.json."""
    import json

    from deerflow.skills import storage as storage_mod
    from deerflow.skills.storage.local_skill_storage import LocalSkillStorage

    skills_root = tmp_path / "skills"
    custom_dir = skills_root / "custom"
    owners_dir = custom_dir / ".owners"
    owners_dir.mkdir(parents=True)

    alice_skill_dir = custom_dir / "alice-skill"
    alice_skill_dir.mkdir()
    (alice_skill_dir / "SKILL.md").write_text("---\nname: alice-skill\ndescription: alice\n---\n")
    (owners_dir / "alice-skill.json").write_text(json.dumps({"owner_id": str(user_env.alice.id)}))

    bob_skill_dir = custom_dir / "bob-skill"
    bob_skill_dir.mkdir()
    (bob_skill_dir / "SKILL.md").write_text("---\nname: bob-skill\ndescription: bob\n---\n")
    (owners_dir / "bob-skill.json").write_text(json.dumps({"owner_id": str(user_env.bob.id)}))

    monkeypatch.setattr(storage_mod, "_default_skill_storage", LocalSkillStorage(host_path=str(skills_root)))
    monkeypatch.setattr(storage_mod, "_default_skill_storage_config", None)

    share_repo = SkillShareRepository(
        __import__("deerflow.persistence.engine", fromlist=["get_session_factory"]).get_session_factory()
    )
    await share_repo.replace_sharees(
        skill_name="alice-skill",
        owner_user_id=str(user_env.alice.id),
        sharee_user_ids={str(user_env.bob.id)},
    )

    from app.gateway.routers.skills import _transfer_custom_skill_ownership

    result = await _transfer_custom_skill_ownership(
        from_user_id=str(user_env.alice.id),
        to_user_id=str(user_env.admin.id),
        share_repo=share_repo,
    )

    assert result["skills_transferred"] == 1
    assert result["shares_transferred"] == 1

    # Alice's skill owner file was rewritten to admin.
    new_alice_owner = json.loads((owners_dir / "alice-skill.json").read_text())
    assert new_alice_owner["owner_id"] == str(user_env.admin.id)
    # Bob's skill owner file is untouched.
    bob_owner = json.loads((owners_dir / "bob-skill.json").read_text())
    assert bob_owner["owner_id"] == str(user_env.bob.id)

    # Share row moved from alice to admin.
    rows = await share_repo.list_sharees_for_skill("alice-skill")
    assert len(rows) == 1
    assert rows[0].owner_user_id == str(user_env.admin.id)


@pytest.mark.asyncio
async def test_delete_user_transfers_skills_to_first_admin(user_env, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: deleting a user via the admin API rewrites their skill owners."""
    import json

    from deerflow.skills import storage as storage_mod
    from deerflow.skills.storage.local_skill_storage import LocalSkillStorage

    skills_root = tmp_path / "skills"
    custom_dir = skills_root / "custom"
    owners_dir = custom_dir / ".owners"
    owners_dir.mkdir(parents=True)

    alice_skill_dir = custom_dir / "alice-skill"
    alice_skill_dir.mkdir()
    (alice_skill_dir / "SKILL.md").write_text("---\nname: alice-skill\ndescription: alice\n---\n")
    (owners_dir / "alice-skill.json").write_text(json.dumps({"owner_id": str(user_env.alice.id)}))

    monkeypatch.setattr(storage_mod, "_default_skill_storage", LocalSkillStorage(host_path=str(skills_root)))
    monkeypatch.setattr(storage_mod, "_default_skill_storage_config", None)

    client = _client_as(user_env.admin)
    with client:
        response = client.delete(f"/api/admin/users/{str(user_env.alice.id)}")
    assert response.status_code == 200

    # Skill owner file rewritten to admin (first admin in list_users ordering).
    new_owner = json.loads((owners_dir / "alice-skill.json").read_text())
    assert new_owner["owner_id"] == str(user_env.admin.id)
