from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.config.effective_config import reset_user_model_cache_for_tests
from deerflow.config.extensions_config import McpServerConfig
from deerflow.extensions_user.image_service import UserImageService
from deerflow.extensions_user.mcp_service import UserMcpService, UserMcpValidationError
from deerflow.extensions_user.schemas import (
    ImageConfigUpdateRequest,
    ImageProviderUpdate,
    McpServerCreateRequest,
    McpServerEnabledRequest,
    McpServerUpdateRequest,
)
from deerflow.extensions_user.secrets import ExtensionSecretStore
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
async def extension_services(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'user_extensions.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    secrets = ExtensionSecretStore()
    mcp_service = UserMcpService(
        UserMcpServerRepository(sf),
        UserMcpServerStateRepository(sf),
        secret_store=secrets,
    )
    image_service = UserImageService(
        UserImageSettingsRepository(sf),
        UserImageProviderRepository(sf),
        secret_store=secrets,
    )
    try:
        yield mcp_service, image_service, secrets
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mcp_service_masks_user_server_secrets(extension_services):
    mcp_service, _, _ = extension_services
    system_servers = {
        "github": McpServerConfig(
            enabled=False,
            type="stdio",
            command="npx",
            args=["-y", "server"],
            env={"GITHUB_TOKEN": "secret-token"},
            description="System github",
        )
    }
    created = await mcp_service.create_server(
        "user-a",
        McpServerCreateRequest(
            name="my-tool",
            enabled=True,
            type="stdio",
            command="echo",
            env={"API_KEY": "user-secret"},
            description="Private tool",
        ),
        system_servers,
    )
    assert created.source == "user"
    assert created.editable is True
    assert created.env.get("API_KEY") == "***"

    view = await mcp_service.get_config_view("user-a", system_servers)
    assert view.mcp_servers["github"].source == "system"
    assert view.mcp_servers["github"].editable is False
    assert view.mcp_servers["github"].env["GITHUB_TOKEN"] == "***"
    assert view.mcp_servers["my-tool"].source == "user"


@pytest.mark.asyncio
async def test_mcp_service_per_user_system_toggle_is_isolated(extension_services):
    mcp_service, _, _ = extension_services
    system_servers = {"github": McpServerConfig(enabled=False, description="System")}

    await mcp_service.set_server_enabled("user-a", "github", McpServerEnabledRequest(enabled=True), system_servers)
    await mcp_service.set_server_enabled("user-b", "github", McpServerEnabledRequest(enabled=False), system_servers)

    view_a = await mcp_service.get_config_view("user-a", system_servers)
    view_b = await mcp_service.get_config_view("user-b", system_servers)
    assert view_a.mcp_servers["github"].enabled is True
    assert view_b.mcp_servers["github"].enabled is False


@pytest.mark.asyncio
async def test_image_service_is_per_user_and_masks_api_key(extension_services):
    _, image_service, secrets = extension_services
    await image_service.update_config(
        "user-a",
        ImageConfigUpdateRequest(
            enabled=True,
            default_provider="openai",
            providers={
                "openai": ImageProviderUpdate(enabled=True, api_key="sk-image-key"),
            },
        ),
    )
    view = await image_service.get_config_view("user-a")
    assert view.enabled is True
    assert view.providers["openai"].has_api_key is True
    assert view.providers["openai"].api_key == "***"

    runtime_config = await image_service.build_user_image_config("user-a")
    assert runtime_config.enabled is True
    assert runtime_config.providers["openai"].api_key == "sk-image-key"

    other_view = await image_service.get_config_view("user-b")
    assert other_view.providers["openai"].has_api_key is False
    assert secrets.decrypt_api_key  # silence lint


@pytest.mark.asyncio
async def test_mcp_service_update_preserves_masked_secrets(extension_services):
    """Round-tripping a masked (``***``) value must preserve the stored secret.

    The GET endpoint masks env/header values; the frontend toggles fields and
    PUTs the full masked record back. ``update_server`` must keep the existing
    secret for masked keys, overwrite on a real value, and reject a masked
    value for a key that does not yet exist.
    """
    mcp_service, _, _ = extension_services
    system_servers: dict[str, McpServerConfig] = {}

    await mcp_service.create_server(
        "user-a",
        McpServerCreateRequest(
            name="secret-tool",
            enabled=True,
            type="stdio",
            command="echo",
            env={"API_KEY": "secret-token", "EXTRA": "keep-me"},
            headers={"X-Signature": "sig-val"},
            description="tool with secrets",
        ),
        system_servers,
    )

    async def runtime_env() -> dict[str, str]:
        merged = await mcp_service.build_user_mcp_servers("user-a", system_servers)
        return merged["secret-tool"].env

    # Initial persisted secret is retrievable in full on the runtime path.
    assert (await runtime_env())["API_KEY"] == "secret-token"

    # Masked round-trip preserves the existing secret.
    await mcp_service.update_server(
        "user-a",
        "secret-tool",
        McpServerUpdateRequest(
            enabled=False,
            env={"API_KEY": "***", "EXTRA": "***"},
            headers={"X-Signature": "***"},
        ),
    )
    env = await runtime_env()
    assert env["API_KEY"] == "secret-token"
    assert env["EXTRA"] == "keep-me"
    assert (await mcp_service.build_user_mcp_servers("user-a", system_servers))["secret-tool"].headers["X-Signature"] == "sig-val"

    # A real value overwrites the stored secret.
    await mcp_service.update_server(
        "user-a",
        "secret-tool",
        McpServerUpdateRequest(env={"API_KEY": "rotated"}),
    )
    assert (await runtime_env())["API_KEY"] == "rotated"

    # A masked value for a key with no existing secret is rejected.
    with pytest.raises(UserMcpValidationError):
        await mcp_service.update_server(
            "user-a",
            "secret-tool",
            McpServerUpdateRequest(env={"NEVER_SET": "***"}),
        )
