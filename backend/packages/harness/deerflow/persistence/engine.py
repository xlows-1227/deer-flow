"""Async SQLAlchemy engine lifecycle management.

Initializes at Gateway startup, provides session factory for
repositories, disposes at shutdown.

When database.backend="memory", init_engine is a no-op and
get_session_factory() returns None. Repositories must check for
None and fall back to in-memory implementations.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def _json_serializer(obj: object) -> str:
    """JSON serializer with ensure_ascii=False for Chinese character support."""
    return json.dumps(obj, ensure_ascii=False)


logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def _auto_create_postgres_db(url: str) -> None:
    """Connect to the ``postgres`` maintenance DB and CREATE DATABASE.

    The target database name is extracted from *url*.  The connection is
    made to the default ``postgres`` database on the same server using
    ``AUTOCOMMIT`` isolation (CREATE DATABASE cannot run inside a
    transaction).
    """
    from sqlalchemy import text
    from sqlalchemy.engine.url import make_url

    parsed = make_url(url)
    db_name = parsed.database
    if not db_name:
        raise ValueError("Cannot auto-create database: no database name in URL")

    # Connect to the default 'postgres' database to issue CREATE DATABASE
    maint_url = parsed.set(database="postgres")
    maint_engine = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        async with maint_engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        logger.info("Auto-created PostgreSQL database: %s", db_name)
    finally:
        await maint_engine.dispose()


async def _run_pending_alembic_revisions(engine: AsyncEngine, backend: str) -> None:
    """Run any pending Alembic revisions on the live engine.

    The dev path (a SQLite file that has been around since before the
    model gained new columns) needs the column to be physically added.
    ``Base.metadata.create_all`` is a no-op for already-existing tables,
    so it would leave the ORM referencing a column the database doesn't
    have, and every query would 500. This helper applies any revisions
    that the env.py scripts haven't already run, in place.

    Implementation note: Alembic's env.py in this project is hard-coded
    to ``create_async_engine`` and only supports online migrations
    through async DBAPI. We hand the engine URL to ``command.upgrade``
    directly — that triggers the full env.py online path (including
    revision bookkeeping) without us needing to poke at the async
    connection ourselves.
    """
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        # Alembic not installed in this environment (shouldn't happen in
        # normal installs). Skip silently rather than fail startup.
        logger.debug("Alembic not available; skipping auto-migration")
        return

    from pathlib import Path

    migrations_dir = Path(__file__).resolve().parent / "migrations"
    if not (migrations_dir / "alembic.ini").exists():
        return

    # Alembic's env.py is hard-coded to ``create_async_engine`` from
    # ``sqlalchemy.ext.asyncio`` so we MUST hand it an async URL —
    # stripping ``+aiosqlite`` to fall back to the sync driver would
    # fail with "asyncio extension requires an async driver".
    async_url = engine.url.render_as_string(hide_password=False)

    cfg = Config(str(migrations_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", async_url)

    try:
        # ``command.upgrade`` is blocking; run it on a thread to keep
        # the asyncio loop responsive. ``upgrade`` itself is a no-op
        # when the database is already at head, so calling it on
        # every startup is safe.
        import asyncio

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, command.upgrade, cfg, "head")
        logger.info("Auto-applied Alembic revisions on %s backend (url=%s)", backend, async_url)
    except Exception as exc:
        # Don't crash startup — the column may already be in place or the
        # migration may be inapplicable. Surface the error so the operator
        # can intervene but keep the service up.
        logger.exception("Auto Alembic upgrade failed: %s", exc)


async def init_engine(
    backend: str,
    *,
    url: str = "",
    echo: bool = False,
    pool_size: int = 5,
    sqlite_dir: str = "",
) -> None:
    """Create the async engine and session factory, then auto-create tables.

    Args:
        backend: "memory", "sqlite", or "postgres".
        url: SQLAlchemy async URL (for sqlite/postgres).
        echo: Echo SQL to log.
        pool_size: Postgres connection pool size.
        sqlite_dir: Directory to create for SQLite (ensured to exist).
    """
    global _engine, _session_factory

    if backend == "memory":
        logger.info("Persistence backend=memory -- ORM engine not initialized")
        return

    if backend == "postgres":
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            raise ImportError(
                "database.backend is set to 'postgres' but asyncpg is not installed.\n"
                "Install it with:\n"
                "    cd backend && uv sync --all-packages --extra postgres\n"
                "On the next `make dev` the postgres extra is auto-detected from\n"
                "config.yaml (database.backend: postgres) and reinstalled, so it\n"
                "will not be wiped again. Set UV_EXTRAS=postgres in .env to opt in\n"
                "explicitly. Or switch to backend: sqlite in config.yaml for\n"
                "single-node deployment."
            ) from None

    if backend == "sqlite":
        import os

        from sqlalchemy import event

        os.makedirs(sqlite_dir or ".", exist_ok=True)
        _engine = create_async_engine(url, echo=echo, json_serializer=_json_serializer)

        # Enable WAL on every new connection. SQLite PRAGMA settings are
        # per-connection, so we wire the listener instead of running PRAGMA
        # once at startup. WAL gives concurrent reads + writers without
        # blocking and is the standard recommendation for any production
        # SQLite deployment (TC-UPG-06 in AUTH_TEST_PLAN.md). The companion
        # ``synchronous=NORMAL`` is the safe-and-fast pairing — fsync only
        # at WAL checkpoint boundaries instead of every commit.
        # Note: we do not set PRAGMA busy_timeout here — Python's sqlite3
        # driver already defaults to a 5-second busy timeout (see the
        # ``timeout`` kwarg of ``sqlite3.connect``), and aiosqlite /
        # SQLAlchemy's aiosqlite dialect inherit that default.  Setting
        # it again would be a no-op.
        @event.listens_for(_engine.sync_engine, "connect")
        def _enable_sqlite_wal(dbapi_conn, _record):  # noqa: ARG001 — SQLAlchemy contract
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA foreign_keys=ON;")
            finally:
                cursor.close()
    elif backend == "postgres":
        _engine = create_async_engine(
            url,
            echo=echo,
            pool_size=pool_size,
            pool_pre_ping=True,
            json_serializer=_json_serializer,
        )
    else:
        raise ValueError(f"Unknown persistence backend: {backend!r}")

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    # Auto-create tables (dev convenience). Production should use Alembic.
    from deerflow.persistence.base import Base

    # Import all models so Base.metadata discovers them.
    # When no models exist yet (scaffolding phase), this is a no-op.
    try:
        import deerflow.persistence.models  # noqa: F401
    except ImportError:
        # Models package not yet available — tables won't be auto-created.
        # This is expected during initial scaffolding or minimal installs.
        logger.debug("deerflow.persistence.models not found; skipping auto-create tables")

    try:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        if backend == "postgres" and "does not exist" in str(exc):
            # Database not yet created — attempt to auto-create it, then retry.
            await _auto_create_postgres_db(url)
            # Rebuild engine against the now-existing database
            await _engine.dispose()
            _engine = create_async_engine(url, echo=echo, pool_size=pool_size, pool_pre_ping=True, json_serializer=_json_serializer)
            _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        else:
            raise

    # ``create_all`` only creates tables that don't exist; it does NOT
    # add new columns to an already-existing table. For dev convenience
    # we then run any pending Alembic revisions so that, e.g., a new
    # ``credential_username`` column shows up on a SQLite database that
    # was created before that field was added to the model.
    await _run_pending_alembic_revisions(_engine, backend)

    logger.info("Persistence engine initialized: backend=%s", backend)


async def init_engine_from_config(config) -> None:
    """Convenience: init engine from a DatabaseConfig object."""
    if config.backend == "memory":
        await init_engine("memory")
        return
    await init_engine(
        backend=config.backend,
        url=config.app_sqlalchemy_url,
        echo=config.echo_sql,
        pool_size=config.pool_size,
        sqlite_dir=config.sqlite_dir if config.backend == "sqlite" else "",
    )


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """Return the async session factory, or None if backend=memory."""
    return _session_factory


def get_engine() -> AsyncEngine | None:
    """Return the async engine, or None if not initialized."""
    return _engine


async def close_engine() -> None:
    """Dispose the engine, release all connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("Persistence engine closed")
    _engine = None
    _session_factory = None
