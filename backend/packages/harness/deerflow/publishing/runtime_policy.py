"""Fail-closed runtime configuration for externally invoked published Agents."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from deerflow.publishing.context import PublishedAgentContext

_SAFE_BASE_CONFIG_KEYS = ("callbacks", "metadata", "run_name", "tags")


def build_published_run_config(
    context: PublishedAgentContext,
    *,
    base_config: RunnableConfig | dict[str, Any] | None = None,
) -> RunnableConfig:
    """Build a run config from trusted context, discarding caller authority.

    Only tracing-related root keys are preserved from ``base_config``. Caller
    ``configurable``/``context`` values are intentionally ignored so request
    payloads cannot select a model, owner, Release, Skill set, connector set,
    memory, plan mode, or subagents.
    """

    config: dict[str, Any] = {}
    source = dict(base_config or {})
    for key in _SAFE_BASE_CONFIG_KEYS:
        if key in source:
            value = source[key]
            config[key] = dict(value) if key == "metadata" and isinstance(value, dict) else value

    connector_ids = sorted({connector_id for connector_id, _capability in context.connector_capabilities})
    config["configurable"] = {
        "published_agent_context": context,
        "model_name": context.model_name,
        "agent_name": None,
        "external_allowed_skills": [],
        "connector_ids": connector_ids,
        "memory_enabled": False,
        "subagent_enabled": False,
        "max_concurrent_subagents": 0,
        "is_plan_mode": False,
        "is_bootstrap": False,
        "thinking_enabled": False,
        "mode": "published",
    }
    return RunnableConfig(**config)

