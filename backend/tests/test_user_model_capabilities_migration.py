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
    assert version == "2026_07_09_umodel_caps"
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
    assert version == "2026_07_09_umodel_caps"
    assert "supports_thinking" in cols
    assert "supports_reasoning_effort" in cols


async def _postgres_ping(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    await engine.dispose()
