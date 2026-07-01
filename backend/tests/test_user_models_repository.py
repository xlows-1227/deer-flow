from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.user_model import UserModelRepository


@pytest_asyncio.fixture()
async def user_model_repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'user_models.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield UserModelRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_model_repository_crud_and_user_isolation(user_model_repo: UserModelRepository):
    created = await user_model_repo.create(
        {
            "user_id": "user-a",
            "name": "my-gpt4o",
            "display_name": "My GPT-4o",
            "provider": "openai",
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "api_key_ref": "encrypted-token",
            "api_key_last_four": "1234",
            "enabled": True,
        }
    )

    assert created["name"] == "my-gpt4o"
    assert await user_model_repo.get(created["id"], user_id="user-b") is None
    assert (await user_model_repo.get(created["id"], user_id="user-a"))["model"] == "gpt-4o"

    updated = await user_model_repo.update(
        created["id"],
        {"display_name": "Updated GPT-4o"},
        user_id="user-a",
    )
    assert updated["display_name"] == "Updated GPT-4o"

    rows = await user_model_repo.list_for_user("user-a")
    assert len(rows) == 1

    deleted = await user_model_repo.delete(created["id"], user_id="user-a")
    assert deleted is True
    assert await user_model_repo.list_for_user("user-a") == []
