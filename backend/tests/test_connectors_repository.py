from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.connector import ConnectorRepository


@pytest_asyncio.fixture()
async def connector_repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield ConnectorRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_connector_repository_crud_and_owner_isolation(connector_repo: ConnectorRepository):
    created = await connector_repo.create_instance(
        {
            "owner_id": "user-a",
            "name": "orders",
            "type": "mysql",
            "config": {"host": "db", "database": "orders"},
            "credential": {"provider": "env", "ref": "MYSQL_URL"},
            "default_policy": {"max_rows": 100},
        }
    )

    assert created["name"] == "orders"
    assert await connector_repo.get_instance(created["id"], owner_id="user-b") is None
    assert (await connector_repo.get_instance(created["id"], owner_id="user-a"))["type"] == "mysql"

    updated = await connector_repo.update_instance(created["id"], {"display_name": "Orders DB"}, owner_id="user-a")
    assert updated["display_name"] == "Orders DB"


@pytest.mark.asyncio
async def test_connector_repository_grants_metadata_and_audit(connector_repo: ConnectorRepository):
    created = await connector_repo.create_instance(
        {
            "owner_id": "user-a",
            "name": "orders",
            "type": "mysql",
            "config": {"host": "db", "database": "orders"},
            "credential": {"provider": "env", "ref": "MYSQL_URL"},
        }
    )

    grant = await connector_repo.create_grant(
        {
            "connector_id": created["id"],
            "subject_type": "skill",
            "subject_id": "analysis",
            "capabilities": ["database.query"],
        }
    )
    assert grant["capabilities"] == ["database.query"]
    assert len(await connector_repo.list_grants(created["id"])) == 1
    assert await connector_repo.update_grant(grant["id"], {"capabilities": ["database.schema.inspect"]}, connector_id="other") is None
    assert await connector_repo.delete_grant(grant["id"], connector_id="other") is False

    await connector_repo.put_metadata(created["id"], "schema", {"schemas": [{"name": "orders"}]})
    assert (await connector_repo.get_metadata(created["id"], "schema"))["metadata_json"]["schemas"][0]["name"] == "orders"

    await connector_repo.append_audit(
        {
            "connector_id": created["id"],
            "connector_type": "mysql",
            "capability": "database.query",
            "operation": "query",
            "decision": "allow",
            "request_summary_json": {},
            "result_summary_json": {"row_count": 1},
        }
    )
    assert (await connector_repo.list_audit(connector_id=created["id"]))[0]["result_summary_json"]["row_count"] == 1
