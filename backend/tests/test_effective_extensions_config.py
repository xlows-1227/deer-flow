from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.config.effective_config import build_effective_extensions_config, reset_user_model_cache_for_tests
from deerflow.config.extensions_config import ExtensionsConfig, ImageGenerationConfig, McpServerConfig
from deerflow.extensions_user.image_service import UserImageService
from deerflow.extensions_user.mcp_service import UserMcpService
from deerflow.extensions_user.schemas import ImageConfigUpdateRequest, ImageProviderUpdate, McpServerCreateRequest
from deerflow.persistence.base import Base
from deerflow.persistence.user_extension import (
    UserImageProviderRepository,
    UserImageSettingsRepository,
    UserMcpServerRepository,
    UserMcpServerStateRepository,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    reset_user_model_cache_for_tests()
    yield
    reset_user_model_cache_for_tests()


@pytest_asyncio.fixture()
async def services(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'effective_extensions.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    import deerflow.extensions_user.image_service as image_module
    import deerflow.extensions_user.mcp_service as mcp_module

    original_image_factory = image_module.make_user_image_service
    original_mcp_factory = mcp_module.make_user_mcp_service
    image_module.make_user_image_service = lambda session_factory=None: UserImageService(
        UserImageSettingsRepository(sf),
        UserImageProviderRepository(sf),
    )
    mcp_module.make_user_mcp_service = lambda session_factory=None: UserMcpService(
        UserMcpServerRepository(sf),
        UserMcpServerStateRepository(sf),
    )
    try:
        yield sf
    finally:
        image_module.make_user_image_service = original_image_factory
        mcp_module.make_user_mcp_service = original_mcp_factory
        await engine.dispose()


@pytest.mark.asyncio
async def test_build_effective_extensions_config_merges_system_and_user_mcp(services, monkeypatch):
    sf = services
    mcp_service = UserMcpService(UserMcpServerRepository(sf), UserMcpServerStateRepository(sf))
    image_service = UserImageService(UserImageSettingsRepository(sf), UserImageProviderRepository(sf))

    await mcp_service.create_server(
        "user-a",
        McpServerCreateRequest(name="private", enabled=True, type="stdio", command="echo"),
        {"github": McpServerConfig(enabled=False, description="System")},
    )
    await image_service.update_config(
        "user-a",
        ImageConfigUpdateRequest(
            enabled=True,
            providers={"openai": ImageProviderUpdate(enabled=True, api_key="sk-test")},
        ),
    )

    base = ExtensionsConfig(
        mcp_servers={"github": McpServerConfig(enabled=True, description="System")},
        image_generation=ImageGenerationConfig(enabled=True),
    )
    monkeypatch.setattr(
        "deerflow.config.effective_config.get_extensions_config",
        lambda: base,
    )

    merged = await build_effective_extensions_config("user-a", base=base)
    assert "github" in merged.mcp_servers
    assert "private" in merged.mcp_servers
    assert merged.image_generation.enabled is True
    assert merged.image_generation.providers["openai"].api_key == "sk-test"
