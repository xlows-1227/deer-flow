"""Load database-backed custom-agent drafts into a run configuration."""

from __future__ import annotations

from typing import Any

from deerflow.config.agents_config import (
    AgentConfig,
    load_agent_config,
    load_agent_soul,
    validate_agent_name,
)
from deerflow.config.paths import get_paths
from deerflow.persistence.engine import get_session_factory
from deerflow.publishing.instructions import compose_agent_instructions

_INTERNAL_AGENT_PREFIX = "__agent_"


def _clear_untrusted_agent_fields(config: dict[str, Any]) -> None:
    """Remove caller-constructible fields reserved for server hydration."""
    for container_name in ("configurable", "context"):
        container = config.get(container_name)
        if not isinstance(container, dict):
            continue
        for key in tuple(container):
            if isinstance(key, str) and key.startswith(_INTERNAL_AGENT_PREFIX):
                container.pop(key, None)


def _owner_legacy_agent_exists(owner_user_id: str, agent_name: str) -> bool:
    """Check only the owner's legacy per-user directory, never another tenant."""
    agent_dir = get_paths().user_agent_dir(owner_user_id, agent_name)
    return agent_dir.is_dir() and (agent_dir / "config.yaml").is_file()


async def hydrate_runtime_agent_config(config: dict[str, Any], *, owner_user_id: str) -> None:
    """Inject the authoritative DB draft before the synchronous graph factory runs.

    Gateway run preparation is async, while LangGraph's graph factory is sync.
    Hydration at this boundary lets the factory consume DraftService state
    without creating another event loop.  A confirmed database miss may use
    only the current owner's read-only legacy files during migration.
    """
    _clear_untrusted_agent_fields(config)
    configurable = config.setdefault("configurable", {})
    context = config.get("context") if isinstance(config.get("context"), dict) else {}
    agent_name = validate_agent_name(context.get("agent_name", configurable.get("agent_name")))
    is_bootstrap = bool(context.get("is_bootstrap", configurable.get("is_bootstrap", False)))
    if agent_name is None or is_bootstrap:
        return
    if get_session_factory() is None:
        configurable["__agent_config_source"] = "filesystem"
        configurable["__agent_files_owner_user_id"] = owner_user_id
        return

    from deerflow.publishing.factory import build_draft_service

    service = build_draft_service()
    if service is None:
        raise RuntimeError("database persistence is enabled but DraftService is unavailable")
    state = await service.get_authoring_state(owner_user_id=owner_user_id, slug=agent_name)
    if state is None:
        if _owner_legacy_agent_exists(owner_user_id, agent_name):
            configurable["__agent_config_source"] = "filesystem"
            configurable["__agent_files_owner_user_id"] = owner_user_id
            configurable["__agent_files_agent_name"] = agent_name
            configurable["__agent_files_strict_owner"] = True
            return
        raise FileNotFoundError(f"Database draft not found for agent '{agent_name}'")
    agent = state["agent"]
    draft = state["draft"]
    configurable["__agent_config_source"] = "database"
    configurable["__agent_config"] = {
        "name": agent_name,
        "description": agent.get("description") or "",
        "model": draft.get("model_name"),
        "tool_groups": list(draft.get("tool_groups") or []),
        "skills": (None if draft.get("skill_selection_mode", "explicit") == "inherit" else [entry["skill_name"] for entry in draft.get("skills") or []]),
    }
    agent_markdown = draft.get("agent_markdown") or ""
    soul_markdown = draft.get("soul_markdown") or ""
    configurable["__agent_instructions"] = compose_agent_instructions(agent_markdown, soul_markdown) if agent_markdown.strip() or soul_markdown.strip() else ""
    configurable["__agent_draft_revision"] = draft.get("revision")


def resolve_runtime_agent_config(
    configurable: dict[str, Any],
    *,
    agent_name: str | None,
    is_bootstrap: bool = False,
) -> AgentConfig | None:
    """Resolve injected DB state or the explicitly owner-scoped legacy fallback."""
    if is_bootstrap or agent_name is None:
        return None
    source = configurable.get("__agent_config_source")
    if source == "database":
        payload = configurable.get("__agent_config")
        if not isinstance(payload, dict):
            raise RuntimeError(f"Hydrated database config missing for agent '{agent_name}'")
        return AgentConfig.model_validate(payload)
    owner_user_id = configurable.get("__agent_files_owner_user_id")
    strict_owner = configurable.get("__agent_files_strict_owner") is True
    return load_agent_config(
        agent_name,
        user_id=owner_user_id if isinstance(owner_user_id, str) else None,
        allow_shared_legacy=not strict_owner,
    )


def resolve_runtime_agent_instructions(configurable: dict[str, Any]) -> str | None:
    """Return trusted DB/strict-owner instructions, or request legacy fallback."""
    source = configurable.get("__agent_config_source")
    if source == "database":
        instructions = configurable.get("__agent_instructions")
        return instructions if isinstance(instructions, str) else ""
    if source == "filesystem" and configurable.get("__agent_files_strict_owner") is True:
        owner_user_id = configurable.get("__agent_files_owner_user_id")
        agent_name = configurable.get("__agent_files_agent_name")
        if not isinstance(owner_user_id, str) or not isinstance(agent_name, str):
            raise RuntimeError("Strict owner legacy source is missing its owner or agent name")
        soul = load_agent_soul(
            agent_name,
            user_id=owner_user_id,
            allow_shared_legacy=False,
        )
        return f"<soul>\n{soul}\n</soul>" if soul else ""
    return None
