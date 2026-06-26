"""Tests for the persistence layer scaffolding.

Tests:
1. DatabaseConfig property derivation (paths, URLs)
2. MemoryRunStore CRUD + user_id filtering
3. Base.to_dict() via inspect mixin
4. Engine init/close lifecycle (memory + SQLite)
5. Postgres missing-dep error message
"""

import sqlite3
import sys
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from deerflow.config.database_config import DatabaseConfig
from deerflow.runtime.runs.store.memory import MemoryRunStore

# -- DatabaseConfig --


class TestDatabaseConfig:
    def test_defaults(self):
        c = DatabaseConfig()
        assert c.backend == "memory"
        assert c.pool_size == 5

    def test_sqlite_paths_unified(self):
        c = DatabaseConfig(backend="sqlite", sqlite_dir="./mydata")
        assert c.sqlite_path.endswith("deerflow.db")
        assert "mydata" in c.sqlite_path
        # Backward-compatible aliases point to the same file
        assert c.checkpointer_sqlite_path == c.sqlite_path
        assert c.app_sqlite_path == c.sqlite_path

    def test_app_sqlalchemy_url_sqlite(self):
        c = DatabaseConfig(backend="sqlite", sqlite_dir="./data")
        url = c.app_sqlalchemy_url
        assert url.startswith("sqlite+aiosqlite:///")
        assert "deerflow.db" in url

    def test_app_sqlalchemy_url_postgres(self):
        c = DatabaseConfig(
            backend="postgres",
            postgres_url="postgresql://u:p@h:5432/db",
        )
        url = c.app_sqlalchemy_url
        assert url.startswith("postgresql+asyncpg://")
        assert "u:p@h:5432/db" in url

    def test_app_sqlalchemy_url_postgres_already_asyncpg(self):
        c = DatabaseConfig(
            backend="postgres",
            postgres_url="postgresql+asyncpg://u:p@h:5432/db",
        )
        url = c.app_sqlalchemy_url
        assert url.count("asyncpg") == 1

    def test_memory_has_no_url(self):
        c = DatabaseConfig(backend="memory")
        with pytest.raises(ValueError, match="No SQLAlchemy URL"):
            _ = c.app_sqlalchemy_url


# -- MemoryRunStore --


class TestMemoryRunStore:
    @pytest.fixture
    def store(self):
        return MemoryRunStore()

    @pytest.mark.anyio
    async def test_put_and_get(self, store):
        await store.put("r1", thread_id="t1", status="pending")
        row = await store.get("r1")
        assert row is not None
        assert row["run_id"] == "r1"
        assert row["status"] == "pending"

    @pytest.mark.anyio
    async def test_get_missing_returns_none(self, store):
        assert await store.get("nope") is None

    @pytest.mark.anyio
    async def test_update_status(self, store):
        await store.put("r1", thread_id="t1")
        await store.update_status("r1", "running")
        assert (await store.get("r1"))["status"] == "running"

    @pytest.mark.anyio
    async def test_update_status_with_error(self, store):
        await store.put("r1", thread_id="t1")
        await store.update_status("r1", "error", error="boom")
        row = await store.get("r1")
        assert row["status"] == "error"
        assert row["error"] == "boom"

    @pytest.mark.anyio
    async def test_list_by_thread(self, store):
        await store.put("r1", thread_id="t1")
        await store.put("r2", thread_id="t1")
        await store.put("r3", thread_id="t2")
        rows = await store.list_by_thread("t1")
        assert len(rows) == 2
        assert all(r["thread_id"] == "t1" for r in rows)

    @pytest.mark.anyio
    async def test_list_by_thread_offset(self, store):
        await store.put("r1", thread_id="t1", created_at="2024-01-01T00:00:00+00:00")
        await store.put("r2", thread_id="t1", created_at="2024-01-02T00:00:00+00:00")
        await store.put("r3", thread_id="t1", created_at="2024-01-03T00:00:00+00:00")
        rows = await store.list_by_thread("t1", limit=1, offset=1)
        assert [r["run_id"] for r in rows] == ["r2"]

    @pytest.mark.anyio
    async def test_list_by_thread_owner_filter(self, store):
        await store.put("r1", thread_id="t1", user_id="alice")
        await store.put("r2", thread_id="t1", user_id="bob")
        rows = await store.list_by_thread("t1", user_id="alice")
        assert len(rows) == 1
        assert rows[0]["user_id"] == "alice"

    @pytest.mark.anyio
    async def test_owner_none_returns_all(self, store):
        await store.put("r1", thread_id="t1", user_id="alice")
        await store.put("r2", thread_id="t1", user_id="bob")
        rows = await store.list_by_thread("t1", user_id=None)
        assert len(rows) == 2

    @pytest.mark.anyio
    async def test_delete(self, store):
        await store.put("r1", thread_id="t1")
        await store.delete("r1")
        assert await store.get("r1") is None

    @pytest.mark.anyio
    async def test_delete_nonexistent_is_noop(self, store):
        await store.delete("nope")  # should not raise

    @pytest.mark.anyio
    async def test_list_pending(self, store):
        await store.put("r1", thread_id="t1", status="pending")
        await store.put("r2", thread_id="t1", status="running")
        await store.put("r3", thread_id="t2", status="pending")
        pending = await store.list_pending()
        assert len(pending) == 2
        assert all(r["status"] == "pending" for r in pending)

    @pytest.mark.anyio
    async def test_list_pending_respects_before(self, store):
        past = "2020-01-01T00:00:00+00:00"
        future = "2099-01-01T00:00:00+00:00"
        await store.put("r1", thread_id="t1", status="pending", created_at=past)
        await store.put("r2", thread_id="t1", status="pending", created_at=future)
        pending = await store.list_pending(before=datetime.now(UTC).isoformat())
        assert len(pending) == 1
        assert pending[0]["run_id"] == "r1"

    @pytest.mark.anyio
    async def test_list_pending_fifo_order(self, store):
        await store.put("r2", thread_id="t1", status="pending", created_at="2024-01-02T00:00:00+00:00")
        await store.put("r1", thread_id="t1", status="pending", created_at="2024-01-01T00:00:00+00:00")
        pending = await store.list_pending()
        assert pending[0]["run_id"] == "r1"

    @pytest.mark.anyio
    async def test_count_inflight_by_user(self, store):
        await store.put("a1", thread_id="t1", user_id="alice", status="pending")
        await store.put("a2", thread_id="t2", user_id="alice", status="running")
        await store.put("a3", thread_id="t3", user_id="alice", status="success")
        await store.put("b1", thread_id="t4", user_id="bob", status="running")
        assert await store.count_inflight_by_user("alice") == 2


# -- Base.to_dict mixin --


class TestBaseToDictMixin:
    @pytest.mark.anyio
    async def test_to_dict_and_exclude(self, tmp_path):
        """Create a temp SQLite DB with a minimal model, verify to_dict."""
        from sqlalchemy import String
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.orm import Mapped, mapped_column

        from deerflow.persistence.base import Base

        class _Tmp(Base):
            __tablename__ = "_tmp_test"
            id: Mapped[str] = mapped_column(String(64), primary_key=True)
            name: Mapped[str] = mapped_column(String(128))

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sf = async_sessionmaker(engine, expire_on_commit=False)
        async with sf() as session:
            session.add(_Tmp(id="1", name="hello"))
            await session.commit()
            obj = await session.get(_Tmp, "1")

            assert obj.to_dict() == {"id": "1", "name": "hello"}
            assert obj.to_dict(exclude={"name"}) == {"id": "1"}
            assert "_Tmp" in repr(obj)

        await engine.dispose()


# -- Engine lifecycle --


class TestEngineLifecycle:
    @pytest.mark.anyio
    async def test_memory_is_noop(self):
        from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

        await init_engine("memory")
        assert get_session_factory() is None
        await close_engine()

    @pytest.mark.anyio
    async def test_sqlite_creates_engine(self, tmp_path):
        from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

        url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
        await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
        sf = get_session_factory()
        assert sf is not None
        async with sf() as session:
            assert session is not None
        await close_engine()
        assert get_session_factory() is None

    @pytest.mark.anyio
    async def test_sqlite_init_migrates_legacy_scheduled_tasks_timezone(self, tmp_path):
        from deerflow.persistence.engine import close_engine, init_engine

        db_path = tmp_path / "legacy.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE scheduled_tasks (
                    id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(64),
                    name VARCHAR(120) NOT NULL,
                    prompt TEXT NOT NULL,
                    repeat_type VARCHAR(20) NOT NULL,
                    execution_time VARCHAR(5) NOT NULL,
                    day_of_week INTEGER,
                    is_enabled BOOLEAN NOT NULL,
                    model_name VARCHAR(128),
                    mode VARCHAR(20) NOT NULL,
                    reasoning_effort VARCHAR(20),
                    last_run_at DATETIME,
                    last_run_status VARCHAR(20),
                    last_run_thread_id VARCHAR(64),
                    last_run_id VARCHAR(64),
                    next_run_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (id)
                )
                """
            )

        url = f"sqlite+aiosqlite:///{db_path}"
        try:
            await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
        finally:
            await close_engine()

        with sqlite3.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(scheduled_tasks)")}

        assert "timezone" in columns

    @pytest.mark.anyio
    async def test_postgres_without_asyncpg_gives_actionable_error(self):
        """If asyncpg is not installed, error message tells user what to do."""
        from deerflow.persistence.engine import init_engine

        with (
            patch.dict(sys.modules, {"asyncpg": None}),
            pytest.raises(ImportError, match="uv sync --all-packages --extra postgres"),
        ):
            await init_engine("postgres", url="postgresql+asyncpg://x:x@localhost/x")
