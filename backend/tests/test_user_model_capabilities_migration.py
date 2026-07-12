from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.engine import _run_pending_alembic_revisions


async def _prepare_sqlite_db(db_path) -> str:
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


def test_user_model_capabilities_migration_on_sqlite(tmp_path):
    url = asyncio.run(_prepare_sqlite_db(tmp_path / "migration.db"))
    engine = asyncio.run(_run_migration_and_inspect(url, backend="sqlite"))
    cols, version = engine
    assert version == "2026_07_12_widen_published_agent_ids"
    assert "supports_thinking" in cols
    assert "supports_reasoning_effort" in cols


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


def test_user_model_capabilities_migration_on_postgres_if_available():
    url = "postgresql+asyncpg://deerflow:deerflow@localhost:5432/deerflow"
    try:
        asyncio.run(_postgres_ping(url))
    except Exception:
        pytest.skip("local postgres unavailable")

    asyncio.run(_prepare_postgres_db(url))
    cols, version = asyncio.run(_run_migration_and_inspect(url, backend="postgres"))
    assert version == "2026_07_12_widen_published_agent_ids"
    assert "supports_thinking" in cols
    assert "supports_reasoning_effort" in cols


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
            await conn.execute(
                text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
            )
            await conn.execute(text("INSERT INTO alembic_version VALUES ('2026_07_01_invite_codes')"))
            await conn.execute(
                text(
                    "CREATE TABLE published_agents (id VARCHAR(32) PRIMARY KEY, owner_user_id VARCHAR(36) NOT NULL, slug VARCHAR(64) NOT NULL, display_name VARCHAR(128) NOT NULL, description TEXT, avatar_ref VARCHAR(256), status VARCHAR(16) NOT NULL DEFAULT 'draft', current_release_id VARCHAR(32), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
                )
            )

    asyncio.run(_seed_old())
    asyncio.run(engine_sync.dispose())
    # Run the full migration chain (it will widen the column via the corrective
    # migration) and inspect the resulting head.
    cols_version = asyncio.run(_run_migration_and_inspect(url, backend="sqlite"))
    _, version = cols_version
    assert version == "2026_07_12_widen_published_agent_ids"
    # Insert a full-length id; under PostgreSQL a too-narrow column rejects this.
    long_id = "pa_" + "a" * 32
    engine_check = create_async_engine(url)

    async def _insert() -> None:
        async with engine_check.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO published_agents (id, owner_user_id, slug, display_name, status, created_at, updated_at) "
                    "VALUES (:id, 'owner', 'slug', 'Name', 'draft', '2026-07-12', '2026-07-12')"
                ),
                {"id": long_id},
            )

    asyncio.run(_insert())
    asyncio.run(engine_check.dispose())

