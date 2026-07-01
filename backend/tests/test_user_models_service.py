from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.config.effective_config import invalidate_user_model_cache, merge_model_configs, reset_user_model_cache_for_tests
from deerflow.config.model_config import ModelConfig
from deerflow.persistence.base import Base
from deerflow.persistence.user_model import UserModelRepository
from deerflow.user_models.schemas import MASKED_API_KEY, UserModelCreateRequest, UserModelUpdateRequest
from deerflow.user_models.secrets import ModelSecretStore
from deerflow.user_models.service import UserModelService, to_model_config


@pytest.fixture(autouse=True)
def _clear_user_model_cache():
    reset_user_model_cache_for_tests()
    yield
    reset_user_model_cache_for_tests()


@pytest_asyncio.fixture()
async def user_model_service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'user_models_service.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    secret_store = ModelSecretStore()
    service = UserModelService(UserModelRepository(async_sessionmaker(engine, expire_on_commit=False)), secret_store=secret_store)
    try:
        yield service, secret_store
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_model_service_encrypts_api_key_and_masks_response(user_model_service):
    service, secret_store = user_model_service
    created = await service.create_model(
        "user-a",
        UserModelCreateRequest(
            name="my-claude",
            display_name="My Claude",
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test-key",
        ),
    )

    assert created.has_api_key is True
    assert created.api_key_last_four == "-key"
    assert "sk-ant" not in created.model_dump_json()

    row = await service._repo.get(created.id, user_id="user-a")
    assert row is not None
    assert secret_store.decrypt_api_key(row["api_key_ref"]) == "sk-ant-test-key"


@pytest.mark.asyncio
async def test_user_model_service_update_preserves_api_key_when_masked(user_model_service):
    service, _secret_store = user_model_service
    created = await service.create_model(
        "user-a",
        UserModelCreateRequest(
            name="my-gpt4o",
            provider="openai",
            model="gpt-4o",
            api_key="sk-test-key-1234",
        ),
    )

    before = await service._repo.get(created.id, user_id="user-a")
    updated = await service.update_model(
        "user-a",
        created.id,
        UserModelUpdateRequest(display_name="Updated", api_key=MASKED_API_KEY),
    )
    after = await service._repo.get(created.id, user_id="user-a")

    assert updated.display_name == "Updated"
    assert after["api_key_ref"] == before["api_key_ref"]


def test_to_model_config_maps_provider_to_use():
    row = {
        "name": "my-openai",
        "display_name": "My OpenAI",
        "provider": "openai",
        "model": "gpt-4o",
        "base_url": "https://example.com/v1",
        "api_key_ref": ModelSecretStore().encrypt_api_key("sk-test"),
    }
    config = to_model_config(row, secret_store=ModelSecretStore())
    assert config.use == "langchain_openai:ChatOpenAI"
    assert config.model == "gpt-4o"
    assert config.base_url == "https://example.com/v1"
    assert config.api_key == "sk-test"


def test_merge_model_configs_appends_and_overrides_by_name():
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig

    base = AppConfig(
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        models=[
            ModelConfig(name="global", use="langchain_openai:ChatOpenAI", model="gpt-4o-mini"),
        ],
    )
    user_models = [
        ModelConfig(name="custom", use="langchain_openai:ChatOpenAI", model="gpt-4o", api_key="sk-user"),
        ModelConfig(name="global", use="langchain_openai:ChatOpenAI", model="gpt-4o", api_key="sk-override"),
    ]
    merged = merge_model_configs(base, user_models)
    names = [model.name for model in merged.models]
    assert names == ["global", "custom"]
    assert merged.get_model_config("global").api_key == "sk-override"
    assert merged.get_model_config("custom").api_key == "sk-user"


def test_invalidate_user_model_cache_clears_entry():
    from deerflow.config import effective_config

    effective_config._user_model_cache["user-a"] = (0.0, [])
    invalidate_user_model_cache("user-a")
    assert "user-a" not in effective_config._user_model_cache
