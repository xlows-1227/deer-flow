from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.engine import _run_pending_alembic_revisions


async def _prepare_database(db_path) -> str:
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO alembic_version VALUES ('2026_07_13_file_shares')"))
        await connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
    await engine.dispose()
    return url


async def _migrate_and_inspect(url: str) -> tuple[set[str], str]:
    engine = create_async_engine(url)
    await _run_pending_alembic_revisions(engine, "sqlite")
    async with engine.connect() as connection:
        columns = {
            row[1]
            for row in (
                await connection.execute(text("PRAGMA table_info(file_publications)"))
            ).fetchall()
        }
        version = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    await engine.dispose()
    return columns, version


def test_file_publications_migration_on_sqlite(tmp_path):
    url = asyncio.run(_prepare_database(tmp_path / "file-publications-migration.db"))
    columns, version = asyncio.run(_migrate_and_inspect(url))

    assert version == "2026_07_15_file_publications"
    assert columns == {
        "id",
        "public_token",
        "owner_user_id",
        "thread_id",
        "source_path",
        "source_identity",
        "created_at",
    }
