"""Tests for invite code persistence and registration integration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.gateway.auth.config import AuthConfig, set_auth_config
from app.gateway.auth.errors import AuthErrorCode
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
from deerflow.persistence.invite_code import InviteCodeRepository
from deerflow.persistence.invite_code.model import InviteCodeRow

_TEST_SECRET = "test-secret-for-invite-code-tests-min32"
_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


@pytest.fixture(autouse=True)
def _persistence_engine(tmp_path):
    from app.gateway import deps

    url = f"sqlite+aiosqlite:///{tmp_path}/invite_code.db"
    asyncio.run(init_engine("sqlite", url=url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    try:
        yield
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        asyncio.run(close_engine())


@pytest.fixture()
def client():
    from support.invite_code_helpers import wire_invite_code_repo

    from app.gateway.app import create_app

    app = create_app()
    wire_invite_code_repo(app)
    return TestClient(app)


def _repo() -> InviteCodeRepository:
    sf = get_session_factory()
    assert sf is not None
    return InviteCodeRepository(sf)


async def _insert_code(code: str, *, used: bool = False) -> None:
    sf = get_session_factory()
    assert sf is not None
    async with sf() as session:
        session.add(InviteCodeRow(code=code, used=used, created_at=datetime.now(UTC)))
        await session.commit()


async def _insert_code_with_timestamps(
    code: str,
    *,
    created_at: datetime,
    used: bool = False,
    used_at: datetime | None = None,
) -> None:
    sf = get_session_factory()
    assert sf is not None
    async with sf() as session:
        session.add(
            InviteCodeRow(code=code, used=used, created_at=created_at, used_at=used_at)
        )
        await session.commit()


def test_migration_seeds_one_hundred_codes():
    assert asyncio.run(_repo().count_all()) == 100


@pytest.mark.asyncio
async def test_claim_complete_release_round_trip():
    code = "TESTCODE01"
    await _insert_code(code)
    repo = _repo()

    assert await repo.claim(code) is True
    assert await repo.claim(code) is False

    await repo.complete(code, "user-123")
    await repo.release(code)

    assert await repo.claim(code) is True


@pytest.mark.asyncio
async def test_claim_unknown_code_returns_false():
    assert await _repo().claim("DOESNOTEXIST") is False


def test_register_requires_invite_code(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "missing-code@test.com", "password": "Tr0ub4dor3a"},
    )
    assert resp.status_code == 422


def test_register_rejects_invalid_invite_code(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "invalid-code@test.com",
            "password": "Tr0ub4dor3a",
            "invite_code": "NOTAREALCODE",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == AuthErrorCode.INVITE_CODE_INVALID


def test_register_consumes_invite_code(client):
    from support.invite_code_helpers import get_unused_invite_code

    invite_code = get_unused_invite_code()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "valid-user@test.com",
            "password": "Tr0ub4dor3a",
            "invite_code": invite_code,
        },
    )
    assert resp.status_code == 201

    assert asyncio.run(_repo().claim(invite_code)) is False


def test_register_rejects_reused_invite_code(client):
    from support.invite_code_helpers import get_unused_invite_code

    invite_code = get_unused_invite_code()
    first = client.post(
        "/api/v1/auth/register",
        json={
            "email": "first-user@test.com",
            "password": "Tr0ub4dor3a",
            "invite_code": invite_code,
        },
    )
    assert first.status_code == 201

    second = client.post(
        "/api/v1/auth/register",
        json={
            "email": "second-user@test.com",
            "password": "AnotherStr0ngPwd!",
            "invite_code": invite_code,
        },
    )
    assert second.status_code == 400
    assert second.json()["detail"]["code"] == AuthErrorCode.INVITE_CODE_INVALID


def test_register_releases_invite_code_on_duplicate_email(client):
    from support.invite_code_helpers import get_unused_invite_code, register_payload

    email = "dup-release@test.com"
    first_code = get_unused_invite_code()
    first = client.post(
        "/api/v1/auth/register",
        json=register_payload(email=email, invite_code=first_code),
    )
    assert first.status_code == 201

    retry_code = get_unused_invite_code()
    duplicate = client.post(
        "/api/v1/auth/register",
        json=register_payload(email=email, password="AnotherStr0ngPwd!", invite_code=retry_code),
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"]["code"] == AuthErrorCode.EMAIL_ALREADY_EXISTS

    retry = client.post(
        "/api/v1/auth/register",
        json=register_payload(email="another-user@test.com", invite_code=retry_code),
    )
    assert retry.status_code == 201


def test_migration_generates_unique_codes():
    sf = get_session_factory()
    assert sf is not None

    async def _fetch_codes() -> list[str]:
        async with sf() as session:
            from sqlalchemy import select

            rows = (await session.execute(select(InviteCodeRow.code))).scalars().all()
            return list(rows)

    codes = asyncio.run(_fetch_codes())
    assert len(codes) == len(set(codes))
    assert all(len(code) == 10 for code in codes)
    assert all(all(ch in _INVITE_ALPHABET for ch in code) for code in codes)


def test_register_normalizes_invite_code_case(client):
    from support.invite_code_helpers import get_unused_invite_code

    invite_code = get_unused_invite_code()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "case-normalize@test.com",
            "password": "Tr0ub4dor3a",
            "invite_code": invite_code.lower(),
        },
    )
    assert resp.status_code == 201
    assert asyncio.run(_repo().claim(invite_code)) is False


_OLD = datetime(2020, 1, 1, tzinfo=UTC)
_MID = datetime(2021, 6, 15, tzinfo=UTC)
_NEW = datetime(2030, 12, 31, tzinfo=UTC)


@pytest.mark.asyncio
async def test_list_paginated_default_keeps_unused_first_then_earliest_created():
    # Used code created earliest must still sort after every unused code.
    await _insert_code_with_timestamps("ZZZUSEDA1", created_at=_OLD, used=True)
    await _insert_code_with_timestamps("ZZZUNUSED", created_at=_OLD)

    rows, total = await _repo().list_paginated(page=1, page_size=200)

    assert total == 102
    assert rows[0]["code"] == "ZZZUNUSED"
    assert rows[-1]["code"] == "ZZZUSEDA1"


@pytest.mark.asyncio
async def test_list_paginated_sort_by_created_at_desc():
    await _insert_code_with_timestamps("ZZZOLDEST", created_at=_OLD)
    await _insert_code_with_timestamps("ZZZNEWEST", created_at=_NEW)

    rows, _ = await _repo().list_paginated(
        page=1, page_size=200, sort_by="created_at", sort_order="desc"
    )

    assert rows[0]["code"] == "ZZZNEWEST"
    assert rows[-1]["code"] == "ZZZOLDEST"


@pytest.mark.asyncio
async def test_list_paginated_sort_by_created_at_asc():
    await _insert_code_with_timestamps("ZZZOLDEST", created_at=_OLD)
    await _insert_code_with_timestamps("ZZZNEWEST", created_at=_NEW)

    rows, _ = await _repo().list_paginated(
        page=1, page_size=200, sort_by="created_at", sort_order="asc"
    )

    assert rows[0]["code"] == "ZZZOLDEST"
    assert rows[-1]["code"] == "ZZZNEWEST"


@pytest.mark.asyncio
async def test_list_paginated_sort_by_used_at_asc_puts_unused_last():
    await _insert_code_with_timestamps(
        "ZZZUSEDOLD", created_at=_OLD, used=True, used_at=_OLD
    )
    await _insert_code_with_timestamps(
        "ZZZUSEDNEW", created_at=_MID, used=True, used_at=_NEW
    )

    rows, _ = await _repo().list_paginated(
        page=1, page_size=200, sort_by="used_at", sort_order="asc"
    )

    assert rows[0]["code"] == "ZZZUSEDOLD"
    assert rows[1]["code"] == "ZZZUSEDNEW"
    # All 100 seeded unused codes (NULL used_at) sink below the used ones.
    assert all(row["used"] for row in rows[:2])
    assert not rows[-1]["used"]


@pytest.mark.asyncio
async def test_list_paginated_sort_by_used_at_desc_puts_unused_last():
    await _insert_code_with_timestamps(
        "ZZZUSEDOLD", created_at=_OLD, used=True, used_at=_OLD
    )
    await _insert_code_with_timestamps(
        "ZZZUSEDNEW", created_at=_MID, used=True, used_at=_NEW
    )

    rows, _ = await _repo().list_paginated(
        page=1, page_size=200, sort_by="used_at", sort_order="desc"
    )

    assert rows[0]["code"] == "ZZZUSEDNEW"
    assert rows[1]["code"] == "ZZZUSEDOLD"
    assert not rows[-1]["used"]


@pytest.mark.asyncio
async def test_list_paginated_invalid_sort_params_fall_back_to_default():
    await _insert_code_with_timestamps("ZZZUNUSED", created_at=_OLD)
    await _insert_code_with_timestamps("ZZZUSEDA1", created_at=_OLD, used=True)

    rows, _ = await _repo().list_paginated(
        page=1, page_size=200, sort_by="bogus", sort_order="desc"
    )

    assert rows[0]["code"] == "ZZZUNUSED"
    assert rows[-1]["code"] == "ZZZUSEDA1"
