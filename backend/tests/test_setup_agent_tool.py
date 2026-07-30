"""Tests for setup_agent tool — validates agent name security and data loss prevention."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.tools.builtins.setup_agent_tool import setup_agent

# --- Helpers ---


class _DummyRuntime(SimpleNamespace):
    context: dict
    tool_call_id: str


def _make_runtime(agent_name: str | None = "test-agent") -> MagicMock:
    runtime = MagicMock()
    runtime.context = {"agent_name": agent_name}
    runtime.tool_call_id = "call_1"
    return runtime


def _make_paths_mock(tmp_path: Path):
    paths = MagicMock()
    paths.base_dir = tmp_path
    paths.agent_dir = lambda name: tmp_path / "agents" / name
    paths.user_agent_dir = lambda user_id, name: tmp_path / "users" / user_id / "agents" / name
    return paths


def _call_setup_agent(tmp_path: Path, soul: str, description: str, agent_name: str = "test-agent"):
    """Call the underlying setup_agent function directly, bypassing langchain tool wrapper."""
    import asyncio

    with patch("deerflow.tools.builtins.setup_agent_tool.get_paths", return_value=_make_paths_mock(tmp_path)):
        return asyncio.run(
            setup_agent.coroutine(
                soul=soul,
                description=description,
                runtime=_make_runtime(agent_name),
            )
        )


def test_sync_setup_rejects_live_persistence_without_starting_new_loop():
    runtime = _make_runtime()
    with patch("deerflow.persistence.engine.get_session_factory", return_value=object()):
        result = setup_agent.func(soul="soul", description="desc", runtime=runtime)
    assert "synchronous embedded client" in result.update["messages"][0].content


def test_persistent_setup_writes_database_only(tmp_path):
    service = MagicMock()
    service.setup_authoring_bundle = AsyncMock(return_value=({"agent": {"id": "pa_1"}, "draft": {}}, []))
    with (
        patch("deerflow.publishing.factory.build_draft_service", return_value=service),
        patch("deerflow.tools.builtins.setup_agent_tool.get_paths") as get_paths,
    ):
        result = asyncio.run(
            setup_agent.coroutine(
                soul="database soul",
                description="database description",
                runtime=_make_runtime(),
                skills=[],
            )
        )

    assert "created successfully" in result.update["messages"][0].content
    service.setup_authoring_bundle.assert_awaited_once()
    get_paths.assert_not_called()
    assert not (tmp_path / "users").exists()


# --- Agent name validation tests ---


def test_setup_agent_rejects_invalid_agent_name_before_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    outside_dir = tmp_path.parent / "outside-target"
    traversal_agent = f"../../../{outside_dir.name}/evil"
    runtime = _DummyRuntime(context={"agent_name": traversal_agent}, tool_call_id="tool-1")

    result = asyncio.run(setup_agent.coroutine(soul="test soul", description="desc", runtime=runtime))

    messages = result.update["messages"]
    assert len(messages) == 1
    assert "Invalid agent name" in messages[0].content
    assert not (tmp_path / "users" / "test-user-autouse" / "agents").exists()
    assert not (outside_dir / "evil" / "SOUL.md").exists()


def test_setup_agent_rejects_absolute_agent_name_before_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    absolute_agent = str(tmp_path / "outside-agent")
    runtime = _DummyRuntime(context={"agent_name": absolute_agent}, tool_call_id="tool-2")

    result = asyncio.run(setup_agent.coroutine(soul="test soul", description="desc", runtime=runtime))

    messages = result.update["messages"]
    assert len(messages) == 1
    assert "Invalid agent name" in messages[0].content
    assert not (tmp_path / "users" / "test-user-autouse" / "agents").exists()
    assert not (Path(absolute_agent) / "SOUL.md").exists()


# --- Data loss prevention tests ---


class TestSetupAgentNoDataLoss:
    """Ensure the no-database file transaction preserves prior agent data."""

    def test_existing_agent_dir_preserved_on_failure(self, tmp_path: Path):
        """If the agent directory already exists and setup fails,
        the directory and its contents must NOT be deleted."""
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        old_soul = agent_dir / "SOUL.md"
        old_soul.write_text("original soul content", encoding="utf-8")
        old_config = agent_dir / "config.yaml"
        old_config.write_text("name: test-agent\ndescription: original\n", encoding="utf-8")

        real_replace = Path.replace

        def _fail_soul_replace(self, target):
            if str(target).endswith("SOUL.md"):
                raise OSError("disk full")
            return real_replace(self, target)

        with patch("deerflow.tools.builtins.setup_agent_tool.get_paths", return_value=_make_paths_mock(tmp_path)):
            with patch.object(Path, "replace", _fail_soul_replace):
                result = asyncio.run(
                    setup_agent.coroutine(
                        soul="new soul",
                        description="desc",
                        runtime=_make_runtime(),
                    )
                )

        assert agent_dir.exists(), "Pre-existing agent directory was deleted on failure"
        assert result.update["messages"][0].content.startswith("Error:")
        assert old_soul.read_text(encoding="utf-8") == "original soul content"
        assert old_config.read_text(encoding="utf-8") == "name: test-agent\ndescription: original\n"
        assert list(agent_dir.glob("*.tmp")) == []
        assert list(agent_dir.glob("*.bak")) == []

    def test_new_agent_dir_cleaned_up_on_failure(self, tmp_path: Path):
        """If the agent directory is newly created and setup fails,
        the directory should be cleaned up."""
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        assert not agent_dir.exists()

        with patch("deerflow.tools.builtins.setup_agent_tool.get_paths", return_value=_make_paths_mock(tmp_path)):
            with patch("yaml.dump", side_effect=OSError("write error")):
                asyncio.run(
                    setup_agent.coroutine(
                        soul="new soul",
                        description="desc",
                        runtime=_make_runtime(),
                    )
                )

        # Newly created directory should be cleaned up
        assert not agent_dir.exists(), "Newly created agent directory was not cleaned up on failure"

    def test_successful_setup_creates_files(self, tmp_path: Path):
        """Happy path: setup_agent creates config.yaml and SOUL.md."""
        _call_setup_agent(tmp_path, soul="# My Agent", description="A test agent")

        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        assert agent_dir.exists()
        assert (agent_dir / "SOUL.md").read_text() == "# My Agent"
        assert (agent_dir / "config.yaml").exists()

    @pytest.mark.no_auto_user
    def test_runtime_user_id_used_when_contextvar_missing(self, tmp_path: Path):
        """setup_agent should not fall back to default when runtime carries user_id."""
        runtime = _DummyRuntime(
            context={"agent_name": "test-agent", "user_id": "auth-user-42"},
            tool_call_id="tool-3",
        )

        with patch("deerflow.tools.builtins.setup_agent_tool.get_paths", return_value=_make_paths_mock(tmp_path)):
            asyncio.run(
                setup_agent.coroutine(
                    soul="# My Agent",
                    description="A test agent",
                    runtime=runtime,
                )
            )

        expected_dir = tmp_path / "users" / "auth-user-42" / "agents" / "test-agent"
        default_dir = tmp_path / "users" / "default" / "agents" / "test-agent"
        assert (expected_dir / "SOUL.md").read_text() == "# My Agent"
        assert not default_dir.exists()
