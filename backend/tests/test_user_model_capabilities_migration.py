from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Boolean, DateTime, Float, Integer, String, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.engine import _run_pending_alembic_revisions


async def _prepare_sqlite_db(db_path: Path) -> str:
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        await conn.execute(text("INSERT INTO alembic_version VALUES ('2026_07_01_invite_codes')"))
        await conn.execute(
            text(
                """
                CREATE TABLE user_models (
                    id VARCHAR(64) PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL,
                    name VARCHAR(80) NOT NULL,
                    display_name VARCHAR(160),
                    provider VARCHAR(32) NOT NULL,
                    model VARCHAR(128) NOT NULL,
                    base_url VARCHAR(512),
                    api_key_ref VARCHAR(512),
                    api_key_last_four VARCHAR(4),
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    deleted_at DATETIME
                )
                """
            )
        )
    await engine.dispose()
    return url


def test_user_model_capabilities_migration_on_sqlite(tmp_path: Path) -> None:
    url = asyncio.run(_prepare_sqlite_db(tmp_path / "migration.db"))
    engine = asyncio.run(_run_migration_and_inspect(url, backend="sqlite"))
    cols, version = engine
    assert version == "2026_07_17_channel_deletion_state"
    assert "supports_thinking" in cols
    assert "supports_reasoning_effort" in cols


def test_draft_skill_mode_migration_defaults_existing_rows_conservatively(tmp_path: Path) -> None:
    url = asyncio.run(_prepare_sqlite_db(tmp_path / "skill-mode.db"))
    asyncio.run(_run_migration_and_inspect(url, backend="sqlite"))

    async def _inspect_default() -> str | None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text("PRAGMA table_info(agent_drafts)"))).fetchall()
            return next(row[4] for row in rows if row[1] == "skill_selection_mode")
        finally:
            await engine.dispose()

    assert asyncio.run(_inspect_default()) in {"'explicit'", '"explicit"'}


def _alembic_config(url: str) -> Config:
    migrations_dir = Path(__file__).resolve().parents[1] / "packages" / "harness" / "deerflow" / "persistence" / "migrations"
    config = Config(str(migrations_dir / "alembic.ini"))
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


async def _agent_channel_columns(url: str, *, backend: str) -> dict[str, tuple[str, bool, str | None, int | None]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            if backend == "sqlite":
                rows = (await conn.execute(text("PRAGMA table_info(agent_channels)"))).fetchall()
                return {
                    str(row[1]): (
                        str(row[2]),
                        not bool(row[3]),
                        None if row[4] is None else str(row[4]),
                        int(str(row[2]).split("(", 1)[1].rstrip(")")) if "(" in str(row[2]) else None,
                    )
                    for row in rows
                }
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT column_name, data_type, is_nullable, column_default, character_maximum_length
                        FROM information_schema.columns
                        WHERE table_name = 'agent_channels'
                        ORDER BY ordinal_position
                        """
                    )
                )
            ).fetchall()
            return {
                str(row[0]): (
                    str(row[1]),
                    str(row[2]) == "YES",
                    None if row[3] is None else str(row[3]),
                    None if row[4] is None else int(row[4]),
                )
                for row in rows
            }
    finally:
        await engine.dispose()


ChannelIngestSchema = tuple[
    set[str],
    dict[str, tuple[str, bool, str | None, int | None]],
    tuple[str, ...],
    dict[str, tuple[tuple[str, ...], bool]],
]


def _normalize_inspected_type(column_type: Any) -> str:
    affinity = getattr(column_type, "_type_affinity", type(column_type))
    for expected_type, normalized in (
        (String, "string"),
        (Integer, "integer"),
        (DateTime, "datetime"),
    ):
        if isinstance(affinity, type) and issubclass(affinity, expected_type):
            return normalized
    return str(getattr(affinity, "__name__", type(column_type).__name__)).lower()


def _has_single_outer_parentheses(value: str) -> bool:
    if not value.startswith("(") or not value.endswith(")"):
        return False
    depth = 0
    quoted = False
    for index, character in enumerate(value):
        if character == "'":
            quoted = not quoted
        elif not quoted and character == "(":
            depth += 1
        elif not quoted and character == ")":
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return False
    return depth == 0 and not quoted


def _normalize_server_default(server_default: Any) -> str:
    value = str(server_default).strip().lower()
    while _has_single_outer_parentheses(value):
        value = value[1:-1].strip()
    value = re.sub(
        r"::(?:[a-z_][a-z0-9_.]*)(?:\s+[a-z_][a-z0-9_.]*)*(?:\(\d+\))?(?:\[\])?\s*$",
        "",
        value,
    ).strip()
    while _has_single_outer_parentheses(value):
        value = value[1:-1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quote = value[0]
        value = value[1:-1].replace(quote * 2, quote)
    return value


@pytest.mark.parametrize(
    ("column_type", "expected"),
    [
        (String(64), "string"),
        (Integer(), "integer"),
        (DateTime(), "datetime"),
        (Boolean(), "boolean"),
        (Float(), "numeric"),
    ],
)
def test_channel_ingest_type_normalization_preserves_type_affinity(column_type: Any, expected: str) -> None:
    assert _normalize_inspected_type(column_type) == expected


@pytest.mark.parametrize(
    ("server_default", "expected"),
    [
        ("'reserved'", "reserved"),
        ("(('reserved'::character varying))", "reserved"),
        ("((0))", "0"),
        ("(false)", "false"),
        ("'unreserved'::character varying", "unreserved"),
        ("10", "10"),
    ],
)
def test_channel_ingest_default_normalization_is_exact(server_default: str, expected: str) -> None:
    assert _normalize_server_default(server_default) == expected


async def _channel_ingest_schema(url: str) -> ChannelIngestSchema:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:

            def inspect_schema(sync_conn: Any) -> ChannelIngestSchema:
                schema = inspect(sync_conn)
                tables = set(schema.get_table_names())
                if "agent_channel_secret_ingests" not in tables:
                    return tables, {}, (), {}
                columns = {
                    str(column["name"]): (
                        _normalize_inspected_type(column["type"]),
                        bool(column["nullable"]),
                        None if column.get("default") is None else _normalize_server_default(column["default"]),
                        getattr(column["type"], "length", None),
                    )
                    for column in schema.get_columns("agent_channel_secret_ingests")
                }
                primary_key = tuple(schema.get_pk_constraint("agent_channel_secret_ingests").get("constrained_columns") or ())
                indexes = {
                    str(index["name"]): (
                        tuple(index.get("column_names") or ()),
                        bool(index.get("unique", False)),
                    )
                    for index in schema.get_indexes("agent_channel_secret_ingests")
                }
                return tables, columns, primary_key, indexes

            return await conn.run_sync(inspect_schema)
    finally:
        await engine.dispose()


def _assert_channel_ingest_contract(schema: ChannelIngestSchema) -> None:
    tables, columns, primary_key, indexes = schema
    assert "agent_channel_secret_ingests" in tables
    assert set(columns) == {
        "secret_ref",
        "agent_id",
        "binding_id",
        "owner_user_id",
        "state",
        "writer_token",
        "writer_generation",
        "writer_lease_expires_at",
        "claim_token",
        "claim_expires_at",
        "not_before",
        "created_at",
        "updated_at",
    }
    assert columns["secret_ref"] == ("string", False, None, 128)
    assert columns["agent_id"] == ("string", False, None, 64)
    assert columns["binding_id"] == ("string", False, None, 64)
    assert columns["owner_user_id"] == ("string", False, None, 128)
    assert columns["state"] == ("string", False, "reserved", 16)
    assert columns["writer_token"] == ("string", True, None, 64)
    assert columns["writer_generation"] == ("integer", False, "0", None)
    assert columns["writer_lease_expires_at"] == ("datetime", True, None, None)
    assert columns["claim_token"] == ("string", True, None, 64)
    assert columns["claim_expires_at"] == ("datetime", True, None, None)
    assert columns["not_before"] == ("datetime", False, None, None)
    assert columns["created_at"] == ("datetime", False, None, None)
    assert columns["updated_at"] == ("datetime", False, None, None)
    assert primary_key == ("secret_ref",)
    assert indexes == {
        "ix_agent_channel_secret_ingests_agent_id": (("agent_id",), False),
        "ix_agent_channel_secret_ingests_binding_id": (("binding_id",), False),
        "ix_agent_channel_secret_ingests_due": (
            ("state", "not_before", "writer_lease_expires_at", "claim_expires_at"),
            False,
        ),
    }


def test_channel_deletion_state_migration_upgrade_and_downgrade_on_sqlite(tmp_path: Path) -> None:
    url = asyncio.run(_prepare_sqlite_db(tmp_path / "channel-deletion-state.db"))
    asyncio.run(_run_migration_and_inspect(url, backend="sqlite"))

    columns = asyncio.run(_agent_channel_columns(url, backend="sqlite"))
    assert columns["delete_previous_status"] == ("VARCHAR(16)", True, None, 16)
    assert columns["runtime_lease_token"] == ("VARCHAR(64)", True, None, 64)
    assert columns["runtime_lease_expires_at"][0:2] == ("DATETIME", True)
    assert columns["runtime_generation"][0:2] == ("INTEGER", False)
    assert columns["runtime_generation"][2] in {"0", "'0'", '"0"'}
    assert columns["health_revision"][0:2] == ("INTEGER", False)
    assert columns["health_revision"][2] in {"0", "'0'", '"0"'}
    assert columns["runtime_stop_requested"][0:2] == ("BOOLEAN", False)
    assert columns["runtime_stop_requested"][2] is not None
    assert columns["runtime_stop_requested"][2].lower().strip("()'\"") in {"0", "false"}
    assert columns["secret_cleanup_ref"] == ("VARCHAR(128)", True, None, 128)
    assert columns["secret_cleanup_reason"] == ("VARCHAR(32)", True, None, 32)
    assert columns["secret_cleanup_not_before"][1] is True
    assert columns["rotation_previous_secret_ref"] == ("VARCHAR(128)", True, None, 128)
    ingest_schema = asyncio.run(_channel_ingest_schema(url))
    _assert_channel_ingest_contract(ingest_schema)

    command.downgrade(_alembic_config(url), "2026_07_14_channel_mappings")
    downgraded = asyncio.run(_agent_channel_columns(url, backend="sqlite"))
    for column_name in (
        "delete_previous_status",
        "runtime_lease_token",
        "runtime_lease_expires_at",
        "runtime_generation",
        "health_revision",
        "runtime_stop_requested",
        "secret_cleanup_ref",
        "secret_cleanup_reason",
        "secret_cleanup_not_before",
        "rotation_previous_secret_ref",
    ):
        assert column_name not in downgraded
    downgraded_tables, _, _, _ = asyncio.run(_channel_ingest_schema(url))
    assert "agent_channel_secret_ingests" not in downgraded_tables

    command.upgrade(_alembic_config(url), "head")
    reupgraded_schema = asyncio.run(_channel_ingest_schema(url))
    _assert_channel_ingest_contract(reupgraded_schema)
    assert reupgraded_schema == ingest_schema


async def _run_migration_and_inspect(url: str, *, backend: str) -> tuple[list[str], str]:
    engine = create_async_engine(url)
    await _run_pending_alembic_revisions(engine, backend)
    async with engine.connect() as conn:
        if backend == "sqlite":
            cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(user_models)"))).fetchall()]
        else:
            cols = [
                row[0]
                for row in (
                    await conn.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_name = 'user_models'
                            ORDER BY ordinal_position
                            """
                        )
                    )
                ).fetchall()
            ]
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    await engine.dispose()
    return cols, version


async def _prepare_postgres_db(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS user_models"))
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        await conn.execute(text("INSERT INTO alembic_version VALUES ('2026_07_01_invite_codes')"))
        await conn.execute(
            text(
                """
                CREATE TABLE user_models (
                    id VARCHAR(64) PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL,
                    name VARCHAR(80) NOT NULL,
                    display_name VARCHAR(160),
                    provider VARCHAR(32) NOT NULL,
                    model VARCHAR(128) NOT NULL,
                    base_url VARCHAR(512),
                    api_key_ref VARCHAR(512),
                    api_key_last_four VARCHAR(4),
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    deleted_at TIMESTAMPTZ
                )
                """
            )
        )
    await engine.dispose()


def test_user_model_capabilities_migration_on_postgres_if_available() -> None:
    url = os.environ.get("TEST_POSTGRES_URL", "postgresql+asyncpg://deerflow:deerflow@localhost:5432/deerflow")
    try:
        asyncio.run(_postgres_ping(url))
    except Exception:
        if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL review gate is required but the test database is unavailable")
        pytest.skip("local postgres unavailable")

    asyncio.run(_prepare_postgres_db(url))
    cols, version = asyncio.run(_run_migration_and_inspect(url, backend="postgres"))
    assert version == "2026_07_17_channel_deletion_state"
    assert "supports_thinking" in cols
    assert "supports_reasoning_effort" in cols
    channel_columns = asyncio.run(_agent_channel_columns(url, backend="postgres"))
    assert channel_columns["delete_previous_status"] == ("character varying", True, None, 16)
    assert channel_columns["runtime_lease_token"] == ("character varying", True, None, 64)
    assert channel_columns["runtime_lease_expires_at"] == ("timestamp with time zone", True, None, None)
    assert channel_columns["runtime_generation"][0:2] == ("integer", False)
    assert channel_columns["runtime_generation"][2] is not None and "0" in channel_columns["runtime_generation"][2]
    assert channel_columns["health_revision"][0:2] == ("integer", False)
    assert channel_columns["health_revision"][2] is not None and "0" in channel_columns["health_revision"][2]
    assert channel_columns["runtime_stop_requested"][0:2] == ("boolean", False)
    assert channel_columns["runtime_stop_requested"][2] is not None
    assert channel_columns["runtime_stop_requested"][2].lower().strip("()'\"") in {"0", "false"}
    assert channel_columns["secret_cleanup_ref"] == ("character varying", True, None, 128)
    assert channel_columns["secret_cleanup_reason"] == ("character varying", True, None, 32)
    assert channel_columns["secret_cleanup_not_before"][0] == "timestamp with time zone"
    assert channel_columns["rotation_previous_secret_ref"] == ("character varying", True, None, 128)
    ingest_schema = asyncio.run(_channel_ingest_schema(url))
    _assert_channel_ingest_contract(ingest_schema)

    command.downgrade(_alembic_config(url), "2026_07_14_channel_mappings")
    downgraded = asyncio.run(_agent_channel_columns(url, backend="postgres"))
    for column_name in (
        "delete_previous_status",
        "runtime_lease_token",
        "runtime_lease_expires_at",
        "runtime_generation",
        "health_revision",
        "runtime_stop_requested",
        "secret_cleanup_ref",
        "secret_cleanup_reason",
        "secret_cleanup_not_before",
        "rotation_previous_secret_ref",
    ):
        assert column_name not in downgraded
    downgraded_tables, _, _, _ = asyncio.run(_channel_ingest_schema(url))
    assert "agent_channel_secret_ingests" not in downgraded_tables
    command.upgrade(_alembic_config(url), "head")
    reupgraded_schema = asyncio.run(_channel_ingest_schema(url))
    _assert_channel_ingest_contract(reupgraded_schema)
    assert reupgraded_schema == ingest_schema


async def _postgres_ping(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    await engine.dispose()


def test_migrated_schema_accepts_full_length_ids(tmp_path):
    """Regression (rereview Critical-1): after running the full migration chain,
    the published-agent ID/FK columns must be wide enough to accept the real
    generated ids (pa_/rel_/skr_ + 32 hex = 36 chars). PostgreSQL enforces
    VARCHAR length, so a too-narrow column would reject the insert."""
    db_path = tmp_path / "ids.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine_sync = create_async_engine(url)

    async def _seed_old() -> None:
        # Simulate a database that already applied the ORIGINAL (too-narrow)
        # published_agents migration: create the table at VARCHAR(32) and stamp
        # alembic at the base revision so the widen migration runs against it.
        async with engine_sync.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO alembic_version VALUES ('2026_07_01_invite_codes')"))
            await conn.execute(
                text(
                    "CREATE TABLE published_agents ("
                    "id VARCHAR(32) PRIMARY KEY, "
                    "owner_user_id VARCHAR(36) NOT NULL, "
                    "slug VARCHAR(64) NOT NULL, "
                    "display_name VARCHAR(128) NOT NULL, "
                    "description TEXT, avatar_ref VARCHAR(256), "
                    "status VARCHAR(16) NOT NULL DEFAULT 'draft', "
                    "current_release_id VARCHAR(32), "
                    "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
                )
            )

    asyncio.run(_seed_old())
    asyncio.run(engine_sync.dispose())
    # Run the full migration chain (it will widen the column via the corrective
    # migration) and inspect the resulting head.
    cols_version = asyncio.run(_run_migration_and_inspect(url, backend="sqlite"))
    _, version = cols_version
    assert version == "2026_07_17_channel_deletion_state"
    import sqlalchemy as sa

    engine_schema = create_async_engine(url)

    async def _assert_reflected_widths() -> None:
        def _columns(sync_conn):
            return {column["name"]: column for column in sa.inspect(sync_conn).get_columns("published_agents")}

        async with engine_schema.connect() as conn:
            columns = await conn.run_sync(_columns)
        assert columns["id"]["type"].length == 64
        assert columns["current_release_id"]["type"].length == 64

    asyncio.run(_assert_reflected_widths())
    asyncio.run(engine_schema.dispose())
    # Insert a full-length id; under PostgreSQL a too-narrow column rejects this.
    long_id = "pa_" + "a" * 32
    engine_check = create_async_engine(url)

    async def _insert() -> None:
        async with engine_check.begin() as conn:
            await conn.execute(
                text("INSERT INTO published_agents (id, owner_user_id, slug, display_name, status, created_at, updated_at) VALUES (:id, 'owner', 'slug', 'Name', 'draft', '2026-07-12', '2026-07-12')"),
                {"id": long_id},
            )

    asyncio.run(_insert())
    asyncio.run(engine_check.dispose())


def test_widen_migration_collapses_duplicate_public_revisions(tmp_path):
    """Rereview Critical-2: an old database may contain duplicate public skill
    revisions (the original unique constraint on NULL owner_user_id did not
    dedupe them). The widen migration must pick a canonical revision, rewrite
    agent_release_skills references, delete the duplicates, and reach the new
    head — otherwise the new unique constraint creation fails."""
    db_path = tmp_path / "dup.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine_seed = create_async_engine(url)

    async def _seed_old_with_duplicates() -> None:
        async with engine_seed.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            # Stamp at the revision just BEFORE the widen migration so only it runs.
            await conn.execute(text("INSERT INTO alembic_version VALUES ('2026_07_12_agent_releases')"))
            # Create skill_revisions with the OLD schema (no owner_scope, unique on
            # skill_name/owner_user_id/content_checksum which allows NULL dupes).
            await conn.execute(
                text(
                    "CREATE TABLE skill_revisions ("
                    "id VARCHAR(32) PRIMARY KEY, "
                    "skill_name VARCHAR(128) NOT NULL, "
                    "owner_user_id VARCHAR(36), "
                    "visibility VARCHAR(16) NOT NULL DEFAULT 'public', "
                    "content_checksum VARCHAR(128) NOT NULL, "
                    "content_ref VARCHAR(256) NOT NULL, "
                    "declared_connector_caps_json JSON NOT NULL DEFAULT '[]', "
                    "created_at DATETIME NOT NULL)"
                )
            )
            # Two duplicate public revisions (same skill_name, NULL owner, same checksum).
            await conn.execute(text("INSERT INTO skill_revisions (id, skill_name, owner_user_id, content_checksum, content_ref, created_at) VALUES ('skr_a', 'reporting', NULL, 'sha256:x', 'cs://x', '2026-07-12')"))
            await conn.execute(text("INSERT INTO skill_revisions (id, skill_name, owner_user_id, content_checksum, content_ref, created_at) VALUES ('skr_b', 'reporting', NULL, 'sha256:x', 'cs://x', '2026-07-12')"))
            # agent_release_skills referencing both duplicates.
            await conn.execute(text("CREATE TABLE agent_release_skills (release_id VARCHAR(32) NOT NULL, skill_revision_id VARCHAR(32) NOT NULL, PRIMARY KEY (release_id, skill_revision_id))"))
            await conn.execute(text("INSERT INTO agent_release_skills VALUES ('rel_1', 'skr_a')"))
            await conn.execute(text("INSERT INTO agent_release_skills VALUES ('rel_1', 'skr_b')"))
            # agent_releases is referenced by agent_release_skills.release_id but not
            # needed for this test; create a minimal stub so FK is not violated.
            await conn.execute(
                text(
                    "CREATE TABLE agent_releases ("
                    "id VARCHAR(32) PRIMARY KEY, agent_id VARCHAR(32) NOT NULL, "
                    "release_no INT NOT NULL, agent_markdown TEXT NOT NULL DEFAULT '', "
                    "soul_markdown TEXT NOT NULL DEFAULT '', model_name VARCHAR(128), "
                    "tool_groups_json JSON NOT NULL DEFAULT '[]', "
                    "quota_overrides_json JSON NOT NULL DEFAULT '{}', "
                    "manifest_checksum VARCHAR(128) NOT NULL, created_by VARCHAR(36) NOT NULL, "
                    "created_at DATETIME NOT NULL)"
                )
            )
            await conn.execute(text("INSERT INTO agent_releases (id, agent_id, release_no, manifest_checksum, created_by, created_at) VALUES ('rel_1', 'pa_1', 1, 'sha', 'user', '2026-07-12')"))

    asyncio.run(_seed_old_with_duplicates())
    asyncio.run(engine_seed.dispose())
    # Run the widen migration; it must collapse duplicates and reach the new head.
    cols_version = asyncio.run(_run_migration_and_inspect(url, backend="sqlite"))
    _, version = cols_version
    assert version == "2026_07_17_channel_deletion_state"

    engine_check = create_async_engine(url)

    async def _verify() -> None:
        async with engine_check.connect() as conn:
            # Only one public revision remains.
            count = (await conn.execute(text("SELECT COUNT(*) FROM skill_revisions WHERE skill_name='reporting' AND content_checksum='sha256:x'"))).scalar_one()
            assert count == 1, f"expected 1 canonical revision, got {count}"
            # Both release->skill references now point at the canonical revision.
            refs = [row[0] for row in (await conn.execute(text("SELECT skill_revision_id FROM agent_release_skills WHERE release_id='rel_1'"))).fetchall()]
            canonical = (await conn.execute(text("SELECT id FROM skill_revisions WHERE skill_name='reporting' AND content_checksum='sha256:x'"))).scalar_one()
            assert set(refs) == {canonical}

    asyncio.run(_verify())
    asyncio.run(engine_check.dispose())


def test_widen_migration_preserves_non_null_constraints(tmp_path):
    """Rereview Important-1: the widen migration must NOT relax NOT NULL
    constraints. Only published_agents.current_release_id is nullable; every
    other widened ID/FK keeps its original NOT NULL."""
    db_path = tmp_path / "nullable.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine_seed = create_async_engine(url)

    async def _seed() -> None:
        async with engine_seed.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO alembic_version VALUES ('2026_07_12_agent_releases')"))
            await conn.execute(
                text(
                    "CREATE TABLE agent_releases ("
                    "id VARCHAR(32) PRIMARY KEY, "
                    "agent_id VARCHAR(32) NOT NULL, "
                    "release_no INT NOT NULL, "
                    "agent_markdown TEXT NOT NULL DEFAULT '', "
                    "soul_markdown TEXT NOT NULL DEFAULT '', "
                    "model_name VARCHAR(128), "
                    "tool_groups_json JSON NOT NULL DEFAULT '[]', "
                    "quota_overrides_json JSON NOT NULL DEFAULT '{}', "
                    "manifest_checksum VARCHAR(128) NOT NULL, "
                    "created_by VARCHAR(36) NOT NULL, "
                    "created_at DATETIME NOT NULL)"
                )
            )

    asyncio.run(_seed())
    asyncio.run(engine_seed.dispose())
    asyncio.run(_run_migration_and_inspect(url, backend="sqlite"))
    import sqlalchemy as sa

    engine_check = create_async_engine(url)

    async def _inspect() -> None:
        def _cols(sync_conn):
            return {c["name"]: c for c in sa.inspect(sync_conn).get_columns("agent_releases")}

        async with engine_check.connect() as conn:
            cols = await conn.run_sync(_cols)
        # The required FK agent_id must remain NOT NULL after the migration
        # (round-3 Important-1). The previous widen migration incorrectly set
        # nullable=True on every column it touched.
        assert cols["agent_id"]["nullable"] is False, "agent_id should stay NOT NULL"
        # manifest_checksum and created_by are also NOT NULL in the base schema.
        assert cols["manifest_checksum"]["nullable"] is False

    asyncio.run(_inspect())
    asyncio.run(engine_check.dispose())


def test_old_long_revision_stamp_upgrades_to_current_head(tmp_path):
    """Fourth-review Critical-1: a SQLite database that applied the original
    long-id revision (2026_07_12_widen_published_agent_ids) must still be able
    to upgrade to the current head. The old revision is retained as a no-op
    stub so the migration graph recognises the stamp."""
    db_path = tmp_path / "old_stamp.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine_seed = create_async_engine(url)

    async def _seed_old_stamp() -> None:
        async with engine_seed.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            # Stamp at the OLD long-id revision (applied by third-round code).
            await conn.execute(text("INSERT INTO alembic_version VALUES ('2026_07_12_widen_published_agent_ids')"))
            # Create skill_revisions with owner_scope already present (the old
            # revision would have added it). The short-id migration must be
            # idempotent and not fail on the already-migrated schema.
            await conn.execute(
                text(
                    "CREATE TABLE skill_revisions ("
                    "id VARCHAR(64) PRIMARY KEY, skill_name VARCHAR(128) NOT NULL, "
                    "owner_user_id VARCHAR(36), owner_scope VARCHAR(64) NOT NULL DEFAULT 'public', "
                    "visibility VARCHAR(16) NOT NULL DEFAULT 'public', "
                    "content_checksum VARCHAR(128) NOT NULL, content_ref VARCHAR(256) NOT NULL, "
                    "declared_connector_caps_json JSON NOT NULL DEFAULT '[]', "
                    "created_at DATETIME NOT NULL)"
                )
            )

    asyncio.run(_seed_old_stamp())
    asyncio.run(engine_seed.dispose())
    cols_version = asyncio.run(_run_migration_and_inspect(url, backend="sqlite"))
    _, version = cols_version
    assert version == "2026_07_17_channel_deletion_state", f"old stamp must upgrade to current head, got {version}"
