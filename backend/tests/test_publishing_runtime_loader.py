from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.publishing.context import DraftSandboxContext
from deerflow.publishing.runtime_loader import (
    hydrate_runtime_agent_config,
    resolve_runtime_agent_config,
    resolve_runtime_agent_instructions,
)


@pytest.mark.asyncio
async def test_draft_sandbox_hydration_uses_frozen_route_snapshot():
    snapshot = DraftSandboxContext(
        owner_user_id="user-a",
        agent_id="pa-1",
        agent_slug="db-agent",
        draft_revision=8,
        description="Snapshot description",
        agent_markdown="UNPUBLISHED SNAPSHOT INSTRUCTION",
        soul_markdown="Snapshot soul",
        model_name="model-snapshot",
        tool_groups=("snapshot-tools",),
        skill_names=("snapshot-skill",),
        connector_capabilities=(("conn-1", "database.query"),),
    )
    config = {
        "configurable": {
            "agent_name": "db-agent",
            "__agent_draft_sandbox_context": snapshot,
        },
        "context": {"agent_name": "db-agent"},
    }

    await hydrate_runtime_agent_config(config, owner_user_id="user-a")

    configurable = config["configurable"]
    resolved = resolve_runtime_agent_config(configurable, agent_name="db-agent")
    assert configurable["__agent_config_source"] == "database"
    assert configurable["__agent_draft_revision"] == 8
    assert resolved.model == "model-snapshot"
    assert resolved.tool_groups is None
    assert resolved.skills == ["snapshot-skill"]
    assert "UNPUBLISHED SNAPSHOT INSTRUCTION" in resolve_runtime_agent_instructions(configurable)


@pytest.mark.asyncio
async def test_hydrate_runtime_agent_config_uses_database_draft():
    service = MagicMock()
    service.get_authoring_state = AsyncMock(
        return_value={
            "agent": {"description": "DB description"},
            "draft": {
                "model_name": "model-db",
                "tool_groups": ["group-db"],
                "skills": [{"skill_name": "skill-db", "source": "public"}],
                "skill_selection_mode": "explicit",
                "agent_markdown": "DB agent instructions",
                "soul_markdown": "DB soul",
                "revision": 7,
            },
        }
    )
    config = {"configurable": {}, "context": {"agent_name": "db-agent"}}
    with (
        patch("deerflow.publishing.runtime_loader.get_session_factory", return_value=object()),
        patch("deerflow.publishing.factory.build_draft_service", return_value=service),
    ):
        await hydrate_runtime_agent_config(config, owner_user_id="user-a")

    configurable = config["configurable"]
    resolved = resolve_runtime_agent_config(configurable, agent_name="db-agent")
    assert configurable["__agent_config_source"] == "database"
    assert configurable["__agent_draft_revision"] == 7
    assert resolved.description == "DB description"
    assert resolved.model == "model-db"
    assert resolved.tool_groups is None
    assert resolved.skills == ["skill-db"]
    assert resolve_runtime_agent_instructions(configurable) == ("<agent_instructions>\nDB agent instructions\n</agent_instructions>\n\n<agent_soul>\nDB soul\n</agent_soul>")


@pytest.mark.asyncio
async def test_hydrate_runtime_agent_config_marks_filesystem_without_database():
    config = {"configurable": {"agent_name": "legacy"}}
    with patch("deerflow.publishing.runtime_loader.get_session_factory", return_value=None):
        await hydrate_runtime_agent_config(config, owner_user_id="user-a")
    assert config["configurable"]["__agent_config_source"] == "filesystem"


@pytest.mark.asyncio
async def test_database_miss_falls_back_only_to_owner_legacy_files(tmp_path):
    owner_dir = tmp_path / "users" / "user-a" / "agents" / "legacy"
    owner_dir.mkdir(parents=True)
    (owner_dir / "config.yaml").write_text("name: legacy\n", encoding="utf-8")
    (owner_dir / "SOUL.md").write_text("OWNER SOUL", encoding="utf-8")
    shared_dir = tmp_path / "agents" / "legacy"
    shared_dir.mkdir(parents=True)
    (shared_dir / "config.yaml").write_text("name: shared-trap\n", encoding="utf-8")
    (shared_dir / "SOUL.md").write_text("SHARED SOUL", encoding="utf-8")
    paths = MagicMock()
    paths.user_agent_dir.side_effect = lambda owner_user_id, agent_name: tmp_path / "users" / owner_user_id / "agents" / agent_name
    paths.agent_dir.side_effect = lambda agent_name: tmp_path / "agents" / agent_name
    service = MagicMock()
    service.get_authoring_state = AsyncMock(return_value=None)
    config = {"configurable": {"agent_name": "legacy"}}

    with (
        patch("deerflow.publishing.runtime_loader.get_session_factory", return_value=object()),
        patch("deerflow.publishing.runtime_loader.get_paths", return_value=paths),
        patch("deerflow.config.agents_config.get_paths", return_value=paths),
        patch("deerflow.publishing.factory.build_draft_service", return_value=service),
    ):
        await hydrate_runtime_agent_config(config, owner_user_id="user-a")
        resolved = resolve_runtime_agent_config(config["configurable"], agent_name="legacy")
        instructions = resolve_runtime_agent_instructions(config["configurable"])

    assert config["configurable"]["__agent_config_source"] == "filesystem"
    assert config["configurable"]["__agent_files_owner_user_id"] == "user-a"
    assert resolved.name == "legacy"
    assert instructions == "<soul>\nOWNER SOUL\n</soul>"

    other_config = {"configurable": {"agent_name": "legacy"}}
    with (
        patch("deerflow.publishing.runtime_loader.get_session_factory", return_value=object()),
        patch("deerflow.publishing.runtime_loader.get_paths", return_value=paths),
        patch("deerflow.config.agents_config.get_paths", return_value=paths),
        patch("deerflow.publishing.factory.build_draft_service", return_value=service),
        pytest.raises(FileNotFoundError, match="Database draft not found"),
    ):
        await hydrate_runtime_agent_config(other_config, owner_user_id="user-b")


@pytest.mark.asyncio
async def test_hydration_removes_caller_agent_internal_fields():
    service = MagicMock()
    service.get_authoring_state = AsyncMock(
        return_value={
            "agent": {"description": "DB description"},
            "draft": {
                "agent_markdown": "DB instructions",
                "soul_markdown": "",
                "model_name": None,
                "tool_groups": ["db-tools"],
                "skills": [],
                "skill_selection_mode": "explicit",
                "revision": 3,
            },
        }
    )
    config = {
        "configurable": {
            "agent_name": "db-agent",
            "__agent_config_source": "filesystem",
            "__agent_instructions": "CALLER CONFIGURABLE",
        },
        "context": {
            "agent_name": "db-agent",
            "__agent_config": {"tool_groups": ["caller-tools"]},
            "__agent_instructions": "CALLER CONTEXT",
            "__agent_draft_revision": 999,
        },
    }
    with (
        patch("deerflow.publishing.runtime_loader.get_session_factory", return_value=object()),
        patch("deerflow.publishing.factory.build_draft_service", return_value=service),
    ):
        await hydrate_runtime_agent_config(config, owner_user_id="user-a")

    assert not any(key.startswith("__agent_") for key in config["context"])
    resolved = resolve_runtime_agent_config(config["configurable"], agent_name="db-agent")
    assert resolved.tool_groups is None
    assert config["configurable"]["__agent_draft_revision"] == 3
    assert resolve_runtime_agent_instructions(config["configurable"]) == ("<agent_instructions>\nDB instructions\n</agent_instructions>")
