"""Tests that the published-agent service factories are fully wired (Critical-2).

The code review found two defects in the Gateway wiring:
1. ``build_publish_service()`` omitted required ``PublishService`` params
   (skills_index, connector_repo, model_index, tool_group_whitelist,
   platform_quota), so even if mounted it could not construct.
2. The Gateway lifespan never placed the services on ``app.state``, so the
   routers answered 503 in production.

These tests pin both fixes: the factory returns a fully-wired service when a DB
engine is live, and the lifespan mounts all three services on ``app.state``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_auto_user


_MINIMAL_CONFIG_YAML = """\
log_level: info
models:
  - name: fake-test-model
    display_name: Fake Test Model
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
    base_url: $OPENAI_API_BASE
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
agents_api:
  enabled: true
title:
  enabled: false
memory:
  enabled: false
database:
  backend: sqlite
run_events:
  backend: memory
tool_groups:
  - name: web
"""


@pytest.fixture
def staged_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stage an isolated DEER_FLOW_HOME + minimal config + reset process singletons."""
    home = tmp_path / "deer-flow-home"
    home.mkdir()
    monkeypatch.setenv("DEER_FLOW_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-not-used")
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.invalid")
    monkeypatch.delenv("DEER_FLOW_SECRET_STORE_KEY", raising=False)

    staged_config = tmp_path / "config.yaml"
    staged_config.write_text(_MINIMAL_CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(staged_config))
    staged_extensions = tmp_path / "extensions_config.json"
    staged_extensions.write_text('{"mcpServers": {}, "skills": {}}', encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(staged_extensions))

    from deerflow.config import app_config as app_config_module
    from deerflow.config import extensions_config as extensions_config_module
    from deerflow.config import paths as paths_module
    from deerflow.persistence import engine as engine_module

    for module, attr in (
        (app_config_module, "_app_config"),
        (app_config_module, "_app_config_path"),
        (app_config_module, "_app_config_mtime"),
        (app_config_module, "_app_config_is_custom"),
        (extensions_config_module, "_extensions_config"),
        (paths_module, "_paths_singleton"),
        (paths_module, "_paths"),
        (engine_module, "_engine"),
        (engine_module, "_session_factory"),
    ):
        monkeypatch.setattr(module, attr, None, raising=False)
    return home


def test_build_publish_service_is_fully_wired(staged_env):
    """When a DB engine is live, build_publish_service returns a fully-wired service."""
    import asyncio

    from deerflow.config.app_config import get_app_config
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config

    config = get_app_config()  # parse staged config so model_index / tool_group_whitelist resolve
    asyncio.run(init_engine_from_config(config.database))
    try:
        sf = get_session_factory()
        assert sf is not None, "session factory should be live after init_engine_from_config"
        from deerflow.publishing.factory import build_publish_service

        service = build_publish_service()
        assert service is not None, "build_publish_service returned None despite a live DB"
        # Critical-2: all required collaborators are populated.
        assert service._model_index is not None and len(service._model_index) >= 1  # noqa: SLF001
        assert "web" in service._tool_group_whitelist  # noqa: SLF001
        assert service._skills is not None  # noqa: SLF001
        assert service._connectors is not None  # noqa: SLF001
        assert service._platform_quota  # noqa: SLF001
    finally:
        asyncio.run(close_engine())


def test_build_draft_service_and_import_service_wired(staged_env):
    import asyncio

    from deerflow.config.app_config import get_app_config
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config

    config = get_app_config()
    asyncio.run(init_engine_from_config(config.database))
    try:
        sf = get_session_factory()
        assert sf is not None
        from deerflow.publishing.factory import build_draft_service, build_import_service

        assert build_draft_service() is not None
        assert build_import_service() is not None
    finally:
        asyncio.run(close_engine())


def test_lifespan_mounts_services_on_app_state(staged_env):
    """The Gateway lifespan must place the three services on app.state (Critical-2).

    The lifespan only runs when the ASGI app actually starts (TestClient context
    manager enters it). We assert the services are mounted while the app is live.
    """
    import logging

    from starlette.testclient import TestClient

    from app.gateway.app import create_app

    application_logger = logging.getLogger("app.gateway.services")
    application_logger.disabled = False
    app = create_app()
    with TestClient(app):
        assert application_logger.disabled is False
        assert hasattr(app.state, "draft_service"), "draft_service not mounted on app.state"
        assert hasattr(app.state, "publish_service"), "publish_service not mounted on app.state"
        assert hasattr(app.state, "import_service"), "import_service not mounted on app.state"
        assert hasattr(app.state, "agent_api_key_repo"), "agent_api_key_repo not mounted on app.state"
        # With a configured sqlite backend they should be fully built.
        assert app.state.draft_service is not None, "draft_service is None despite sqlite backend"
        assert app.state.publish_service is not None, "publish_service is None despite sqlite backend"
        assert app.state.import_service is not None, "import_service is None despite sqlite backend"
        assert app.state.agent_api_key_repo is not None, "agent_api_key_repo is None despite sqlite backend"
        assert app.state.published_agent_resolver is not None, "resolver is None despite sqlite backend"
        assert app.state.published_channel_runtime is not None, "published channel runtime is not wired"
        assert app.state.feishu_supervisor is None, "missing secret key should disable only the DB Supervisor"
        from deerflow.publishing.skills_index import ConnectorServiceRepo

        assert isinstance(app.state.published_agent_resolver._connectors, ConnectorServiceRepo)
        paths = {route.path for route in app.routes}
        assert "/api/published-agents/{agent_id}/keys" in paths
