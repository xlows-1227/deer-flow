from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

from deerflow.agents.lead_agent import agent as lead_agent_module
from deerflow.agents.middlewares import dynamic_context_middleware as dynamic_context_module
from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.runtime_policy import build_published_run_config
from deerflow.tools import tools as tools_module


def _tool(name: str) -> StructuredTool:
    def invoke() -> str:
        return name

    return StructuredTool.from_function(invoke, name=name, description=f"{name} tool")


def _context(*, allowed_tool_names: tuple[str, ...] | None = ("read_file",)) -> PublishedAgentContext:
    return PublishedAgentContext(
        owner_user_id="owner-a",
        agent_id="pa_1",
        release_id="rel_1",
        source="api",
        credential_id="key_1",
        external_actor="actor-hash",
        conversation_scope="conv_1",
        skill_revision_ids=("skr_1",),
        connector_capabilities=(("conn_1", "database.query"),),
        tool_groups=("safe",),
        model_name="trusted-model",
        instructions="trusted instructions",
        effective_quota=SimpleNamespace(max_tokens_per_run=1000),
        correlation_id="corr_1",
        idempotency_key="idem_1",
        allowed_tool_names=allowed_tool_names,
    )


def test_build_published_run_config_ignores_untrusted_runtime_fields() -> None:
    config = build_published_run_config(
        _context(),
        base_config={
            "configurable": {
                "memory_enabled": True,
                "model_name": "attacker-model",
                "model": "attacker-model",
                "skills": ["attacker-skill"],
                "external_allowed_skills": ["attacker-skill"],
                "subagent_enabled": True,
                "is_plan_mode": True,
                "agent_name": "other-owner-agent",
            },
            "metadata": {"caller_trace": "keep"},
        },
    )

    configurable = config["configurable"]
    assert configurable["published_agent_context"] is not None
    assert configurable["model_name"] == "trusted-model"
    assert configurable["memory_enabled"] is False
    assert configurable["subagent_enabled"] is False
    assert configurable["is_plan_mode"] is False
    assert configurable["agent_name"] is None
    assert configurable["external_allowed_skills"] == []
    assert configurable["connector_ids"] == ["conn_1"]
    assert configurable["mode"] == "published"
    assert "skills" not in configurable
    assert config["metadata"] == {"caller_trace": "keep"}
    assert config["context"]["user_id"] == "owner-a"
    assert config["context"]["connector_ids"] == ["conn_1"]
    assert config["context"]["connector_capabilities"] == {"conn_1": ["database.query"]}


def test_published_middlewares_exclude_memory_and_memory_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    app_config = SimpleNamespace(
        memory=SimpleNamespace(enabled=True, injection_enabled=True),
        token_usage=SimpleNamespace(enabled=False),
        tool_search=SimpleNamespace(enabled=False),
        loop_detection=SimpleNamespace(enabled=True),
        safety_finish_reason=SimpleNamespace(enabled=False),
        get_model_config=lambda name: SimpleNamespace(supports_vision=False),
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(lead_agent_module, "build_lead_runtime_middlewares", lambda **kwargs: [])
    monkeypatch.setattr(
        lead_agent_module,
        "_create_summarization_middleware",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError(f"Published runtime must not create a summarization model: {kwargs}")),
    )
    monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda enabled: None)
    monkeypatch.setattr(
        lead_agent_module,
        "TitleMiddleware",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError(f"Published runtime must not create a title model: {kwargs}")),
    )
    monkeypatch.setattr(lead_agent_module.LoopDetectionMiddleware, "from_config", lambda config: "loop")
    monkeypatch.setattr(lead_agent_module, "MemoryMiddleware", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("MemoryMiddleware must not be constructed")))
    monkeypatch.setattr(
        dynamic_context_module,
        "DynamicContextMiddleware",
        lambda *args, include_memory=True, **kwargs: captured.setdefault("dynamic_include_memory", include_memory) or "dynamic",
    )

    middlewares = lead_agent_module._build_middlewares(
        build_published_run_config(_context()),
        model_name="trusted-model",
        app_config=app_config,
    )

    assert captured == {"dynamic_include_memory": False}
    assert "title" not in middlewares
    token_middleware = next(item for item in middlewares if isinstance(item, lead_agent_module.TokenUsageMiddleware))
    assert token_middleware.max_tokens_per_run == 1000
    assert middlewares.index(token_middleware) > middlewares.index("loop")


def test_published_dynamic_context_never_loads_owner_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "deerflow.agents.lead_agent.prompt._get_memory_context",
        lambda *args, **kwargs: "<memory>OWNER USER.md SECRET</memory>",
    )
    middleware = dynamic_context_module.DynamicContextMiddleware(
        app_config=SimpleNamespace(memory=SimpleNamespace(injection_enabled=True)),
        include_memory=False,
    )

    reminder = middleware._build_full_reminder()

    assert "<memory>" not in reminder
    assert "OWNER USER.md SECRET" not in reminder
    assert "<current_date>" in reminder


def test_published_tools_are_platform_group_and_skill_intersection(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = {
        "safe.read": _tool("read_file"),
        "safe.write": _tool("write_file"),
        "admin.manage": _tool("connector_manage"),
        "other.search": _tool("web_search"),
    }
    config = SimpleNamespace(
        tools=[
            SimpleNamespace(name="read_file", group="safe", use="safe.read"),
            SimpleNamespace(name="write_file", group="safe", use="safe.write"),
            SimpleNamespace(name="connector_manage", group="safe", use="admin.manage"),
            SimpleNamespace(name="web_search", group="other", use="other.search"),
        ],
        models=[SimpleNamespace(name="trusted-model")],
        get_model_config=lambda name: SimpleNamespace(supports_vision=False),
        skill_evolution=SimpleNamespace(enabled=True),
        extensions=SimpleNamespace(get_enabled_mcp_servers=lambda: {"attacker": {}}),
        connectors=SimpleNamespace(enabled=False),
        tool_search=SimpleNamespace(enabled=True),
        acp_agents={"admin-agent": {}},
    )
    monkeypatch.setattr(tools_module, "resolve_variable", lambda use, base: configured[use])
    monkeypatch.setattr(tools_module, "is_host_bash_allowed", lambda config: True)
    monkeypatch.setattr(tools_module, "has_enabled_image_generation_provider", lambda config: False)

    result = tools_module.get_available_tools(
        groups=["other"],
        include_mcp=True,
        subagent_enabled=True,
        app_config=config,
        published_context=_context(),
    )

    assert [tool.name for tool in result] == ["read_file"]


def test_published_context_rejects_memory_override_even_when_reconstructed() -> None:
    values = dict(_context().__dict__)
    values["memory_enabled"] = True
    with pytest.raises(ValueError, match="memory-free"):
        PublishedAgentContext(**values)
