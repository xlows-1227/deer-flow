from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deerflow.agents.lead_agent import agent as lead_agent_module
from deerflow.config.agents_config import AgentConfig
from deerflow.runtime.runs.worker import _should_use_flash_direct_path


def test_forced_skill_is_exclusive_in_prompt_and_metadata(monkeypatch):
    monkeypatch.setattr(lead_agent_module, "_resolve_model_name", lambda x=None, **kwargs: "default-model")
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: "model")
    monkeypatch.setattr(lead_agent_module, "_build_middlewares", lambda *args, **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(lead_agent_module, "load_agent_config", lambda x: AgentConfig(name="test", skills=["sales-report", "other"]))
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        lead_agent_module,
        "_load_enabled_skills_for_tool_policy",
        lambda available_skills, *, app_config: [
            SimpleNamespace(
                name="sales-report",
                description="Sales report",
                category="public",
                skill_path="/skills/sales-report",
                skill_file=Path("/skills/sales-report/SKILL.md"),
                allowed_tools=None,
                enabled=True,
            )
        ],
    )
    captured = {}
    monkeypatch.setattr(lead_agent_module, "apply_prompt_template", lambda **kwargs: captured.update(kwargs) or "prompt")
    config = MagicMock()
    config.get_model_config.return_value = SimpleNamespace(supports_thinking=False, supports_vision=False)

    runtime = {"configurable": {"agent_name": "test", "skill_name": "sales-report"}}
    lead_agent_module._make_lead_agent(runtime, app_config=config)

    assert captured["available_skills"] == {"sales-report"}
    assert runtime["metadata"]["available_skills"] == ["sales-report"]


def test_forced_skill_not_allowed_by_agent_fails_closed():
    with pytest.raises(ValueError, match="not allowed"):
        lead_agent_module._resolve_available_skill_names(
            AgentConfig(name="test", skills=["other"]),
            False,
            "sales-report",
            app_config=MagicMock(),
        )


def test_external_skill_whitelist_applies_without_forced_skill():
    assert lead_agent_module._resolve_available_skill_names(
        AgentConfig(name="test", skills=["sales-report", "other"]),
        False,
        None,
        app_config=MagicMock(),
        external_allowed_skills=["sales-report"],
    ) == {"sales-report"}
    assert (
        lead_agent_module._resolve_available_skill_names(
            None,
            False,
            None,
            app_config=MagicMock(),
            external_allowed_skills=[],
        )
        == set()
    )


def test_forced_skill_disables_flash_direct_path(monkeypatch):
    monkeypatch.setattr("deerflow.runtime.runs.worker._thread_has_historical_uploads", lambda thread_id: False)
    assert (
        _should_use_flash_direct_path(
            graph_input={"messages": []},
            config={"configurable": {"mode": "flash", "skill_name": "sales-report"}},
            thread_id="thread-1",
            interrupt_before=None,
            interrupt_after=None,
        )
        is False
    )
