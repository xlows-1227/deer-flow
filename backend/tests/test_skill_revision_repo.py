"""Repository tests for skill_revisions (immutable, content-deduplicated).

Covers F1.2 acceptance: the same ``(skill_name, owner_user_id, content_checksum)``
reuses the existing revision id; a changed checksum produces a new revision
while leaving prior revisions untouched; private skills carry an owner.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.skill_revision import SkillRevisionRepository


@pytest_asyncio.fixture()
async def skill_repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'skills.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield SkillRevisionRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_or_create_reuses_same_checksum(skill_repo):
    a = await skill_repo.get_or_create(
        skill_name="reporting",
        owner_user_id=None,
        visibility="public",
        content_checksum="sha256:abc",
        content_ref="cs://reporting/sha256:abc",
        declared_connector_caps=[],
    )
    b = await skill_repo.get_or_create(
        skill_name="reporting",
        owner_user_id=None,
        visibility="public",
        content_checksum="sha256:abc",
        content_ref="cs://reporting/sha256:abc",
        declared_connector_caps=[],
    )
    assert a["id"] == b["id"]  # deduped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("visibility", "private"),
        ("content_ref", "cs://reporting/different"),
        ("declared_connector_caps", ["database.write"]),
    ],
)
async def test_get_or_create_rejects_metadata_mismatch_for_same_checksum(skill_repo, override, value):
    values = {
        "skill_name": "reporting",
        "owner_user_id": None,
        "visibility": "public",
        "content_checksum": "sha256:invariant",
        "content_ref": "cs://reporting/invariant",
        "declared_connector_caps": ["database.query"],
    }
    await skill_repo.get_or_create(**values)
    values[override] = value

    with pytest.raises(RuntimeError, match="metadata mismatch"):
        await skill_repo.get_or_create(**values)


@pytest.mark.asyncio
async def test_changed_checksum_creates_new_revision(skill_repo):
    first = await skill_repo.get_or_create(
        skill_name="reporting",
        owner_user_id=None,
        visibility="public",
        content_checksum="sha256:v1",
        content_ref="cs://reporting/v1",
        declared_connector_caps=["database.query"],
    )
    second = await skill_repo.get_or_create(
        skill_name="reporting",
        owner_user_id=None,
        visibility="public",
        content_checksum="sha256:v2",
        content_ref="cs://reporting/v2",
        declared_connector_caps=["database.query"],
    )
    assert first["id"] != second["id"]
    # The original revision is still retrievable unchanged.
    again = await skill_repo.get(first["id"], owner_user_id="any-owner")
    assert again is not None
    assert again["content_checksum"] == "sha256:v1"


@pytest.mark.asyncio
async def test_private_skill_records_owner(skill_repo):
    rev = await skill_repo.get_or_create(
        skill_name="private-tool",
        owner_user_id="user-a",
        visibility="private",
        content_checksum="sha256:priv",
        content_ref="cs://private-tool/priv",
        declared_connector_caps=[],
    )
    assert rev["owner_user_id"] == "user-a"
    assert rev["visibility"] == "private"
    # Two different owners each producing the "same" private skill content get
    # distinct revisions (owner is part of the uniqueness key).
    other = await skill_repo.get_or_create(
        skill_name="private-tool",
        owner_user_id="user-b",
        visibility="private",
        content_checksum="sha256:priv",
        content_ref="cs://private-tool/priv",
        declared_connector_caps=[],
    )
    assert rev["id"] != other["id"]

    assert await skill_repo.get(rev["id"], owner_user_id="user-b") is None
    assert (await skill_repo.get(rev["id"], owner_user_id="user-a"))["id"] == rev["id"]


@pytest.mark.asyncio
async def test_list_by_skill_name(skill_repo):
    await skill_repo.get_or_create(
        skill_name="x",
        owner_user_id=None,
        visibility="public",
        content_checksum="sha256:1",
        content_ref="cs://x/1",
        declared_connector_caps=[],
    )
    await skill_repo.get_or_create(
        skill_name="x",
        owner_user_id=None,
        visibility="public",
        content_checksum="sha256:2",
        content_ref="cs://x/2",
        declared_connector_caps=[],
    )
    revs = await skill_repo.list_by_skill("x")
    assert len(revs) == 2


@pytest.mark.asyncio
async def test_public_skill_revision_dedup_protected_against_null_owner(skill_repo):
    """Regression (rereview Important-2): two public-skill get_or_create calls
    with identical content must reuse a single revision. The original unique
    constraint on (skill_name, owner_user_id, content_checksum) did not protect
    this because SQL NULLs are distinct; owner_scope ('public') fixes it."""
    a = await skill_repo.get_or_create(
        skill_name="public-reporting",
        owner_user_id=None,
        visibility="public",
        content_checksum="sha256:same",
        content_ref="cs://public-reporting/same",
        declared_connector_caps=[],
    )
    b = await skill_repo.get_or_create(
        skill_name="public-reporting",
        owner_user_id=None,
        visibility="public",
        content_checksum="sha256:same",
        content_ref="cs://public-reporting/same",
        declared_connector_caps=[],
    )
    assert a["id"] == b["id"], "public skill revisions with identical content must dedupe"
    # And the owner_scope is recorded as 'public'.
    assert a["owner_scope"] == "public"


@pytest.mark.asyncio
async def test_private_skill_owner_scope_is_owner_id(skill_repo):
    rev = await skill_repo.get_or_create(
        skill_name="private-tool",
        owner_user_id="user-a",
        visibility="private",
        content_checksum="sha256:priv",
        content_ref="cs://private-tool/priv",
        declared_connector_caps=[],
    )
    assert rev["owner_scope"] == "user-a"


@pytest.mark.asyncio
async def test_concurrent_get_or_create_in_session_dedupes(skill_repo, tmp_path):
    """Sixth-review Minor-2: two truly concurrent _get_or_create_in_session
    calls for the same (skill_name, owner_scope, checksum) must both succeed and
    return the same canonical revision. Uses asyncio.gather with two independent
    sessions sharing one engine to force a real unique-key race."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    both_missed = asyncio.Event()
    miss_count = 0
    miss_lock = asyncio.Lock()

    async def _barrier_after_select_miss() -> None:
        nonlocal miss_count
        async with miss_lock:
            miss_count += 1
            if miss_count == 2:
                both_missed.set()
        await both_missed.wait()

    skill_repo._after_initial_miss = _barrier_after_select_miss  # noqa: SLF001

    async def _create_revision() -> str:
        async with sf() as session:
            async with session.begin():
                row = await skill_repo._get_or_create_in_session(  # noqa: SLF001
                    session,
                    skill_name="race-skill",
                    owner_user_id=None,
                    visibility="public",
                    content_checksum="sha256:race",
                    content_ref="cs://race/race",
                    declared_connector_caps=[],
                )
            return row.id

    try:
        # Launch both concurrently — both SELECT miss, both INSERT, one wins
        # the unique constraint, the SAVEPOINT lets the loser recover.
        id_a, id_b = await asyncio.gather(_create_revision(), _create_revision())
        assert miss_count == 2, "both transactions must pass the initial SELECT-miss barrier"
        assert id_a == id_b, "concurrent get_or_create must dedupe to the same revision id"
    finally:
        await engine.dispose()
