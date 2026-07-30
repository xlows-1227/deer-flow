"""update_agent tool — persist updates to the current custom-agent draft.

Bound to the lead agent only when ``runtime.context['agent_name']`` is set
(i.e. inside an existing custom agent's chat). The default agent does not see
this tool, and the bootstrap flow continues to use ``setup_agent`` for the
initial creation handshake.

With persistence enabled, DraftService is the only write target and the Gateway
runtime reads the resulting draft directly. The per-user files remain a legacy
fallback only for embedded/CLI processes without a database.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from deerflow.config.agents_config import AgentConfig, load_agent_config, validate_agent_name
from deerflow.config.app_config import get_app_config
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)


@tool(parse_docstring=True)
async def update_agent(
    runtime: Runtime,
    soul: str | None = None,
    description: str | None = None,
    skills: list[str] | None = None,
    tool_groups: list[str] | None = None,
    model: str | None = None,
) -> Command:
    """Persist updates to the current custom agent's authoring draft.

    Use this when the user asks to refine the agent's identity, description,
    skill whitelist, tool-group whitelist, or default model. Only the fields
    you explicitly pass are updated; omitted fields keep their existing values.

    Pass ``soul`` as the FULL replacement SOUL.md content — there is no patch
    semantics, so always start from the current SOUL and apply your edits.

    Pass ``skills=[]`` to disable all skills for this agent. Omit ``skills``
    entirely to keep the existing whitelist.

    Args:
        soul: Optional full replacement SOUL.md content.
        description: Optional new one-line description.
        skills: Optional skill whitelist. ``[]`` = no skills, omit = unchanged.
        tool_groups: Optional tool-group whitelist. ``[]`` = empty, omit = unchanged.
        model: Optional model override (must match a configured model name).

    Returns:
        Command with a ToolMessage describing the result. Changes take effect
        on the next user turn (when the lead agent is rebuilt from the fresh
        database draft or the legacy no-database files).
    """
    tool_call_id = runtime.tool_call_id
    agent_name_raw: str | None = runtime.context.get("agent_name") if runtime.context else None

    def _err(message: str) -> Command:
        return Command(update={"messages": [ToolMessage(content=f"Error: {message}", tool_call_id=tool_call_id)]})

    if soul is None and description is None and skills is None and tool_groups is None and model is None:
        return _err("No fields provided. Pass at least one of: soul, description, skills, tool_groups, model.")

    try:
        agent_name = validate_agent_name(agent_name_raw)
    except ValueError as e:
        return _err(str(e))

    if not agent_name:
        return _err("update_agent is only available inside a custom agent's chat. There is no agent_name in the current runtime context, so there is nothing to update. If you are inside the bootstrap flow, use setup_agent instead.")

    # Resolve the active user so that updates only affect this user's agent.
    # ``resolve_runtime_user_id`` prefers ``runtime.context["user_id"]`` (set by
    # the gateway from the auth-validated request) and falls back to the
    # contextvar, then DEFAULT_USER_ID. This matches setup_agent so a user
    # creating an agent and later refining it always touches the same files,
    # even if the contextvar gets lost across an async/thread boundary
    # (issue #2782 / #2862 class of bugs).
    user_id = resolve_runtime_user_id(runtime)

    # Reject an unknown ``model`` *before* touching the filesystem. Otherwise
    # ``_resolve_model_name`` silently falls back to the default at runtime
    # and the user sees confusing repeated warnings on every later turn.
    if model is not None and get_app_config().get_model_config(model) is None:
        return _err(f"Unknown model '{model}'. Pass a model name that exists in config.yaml's models section.")

    try:
        from deerflow.persistence.engine import get_session_factory
        from deerflow.publishing.factory import build_draft_service

        service = build_draft_service()
        if service is None and get_session_factory() is not None:
            return _err("Database persistence is enabled but DraftService is unavailable.")
        if service is not None:
            state = await service.get_authoring_state(owner_user_id=user_id, slug=agent_name)
            if state is None:
                return _err(f"Agent '{agent_name}' does not exist for the current user. Use setup_agent to create a new agent first.")
            agent = state["agent"]
            draft = state["draft"]
            existing_cfg = AgentConfig(
                name=agent_name,
                description=agent.get("description") or "",
                model=draft.get("model_name"),
                tool_groups=list(draft.get("tool_groups") or []),
                skills=(None if draft.get("skill_selection_mode", "explicit") == "inherit" else [entry["skill_name"] for entry in draft.get("skills") or []]),
            )
        else:
            paths = get_paths()
            agent_dir = paths.user_agent_dir(user_id, agent_name)
            if not agent_dir.exists() and paths.agent_dir(agent_name).exists():
                return _err(f"Agent '{agent_name}' only exists in the legacy shared layout and is not scoped to a user. Run scripts/migrate_user_isolation.py to move legacy agents into the per-user layout before updating.")
            existing_cfg = load_agent_config(agent_name, user_id=user_id)
    except FileNotFoundError:
        return _err(f"Agent '{agent_name}' does not exist for the current user. Use setup_agent to create a new agent first.")
    except ValueError as e:
        return _err(f"Agent '{agent_name}' has an unreadable config: {e}")
    except Exception as e:
        return _err(f"Failed to load agent '{agent_name}': {e}")

    if existing_cfg is None:
        return _err(f"Agent '{agent_name}' could not be loaded.")

    updated_fields: list[str] = []

    # Force the on-disk ``name`` to match the directory we are writing into,
    # even if ``existing_cfg.name`` had drifted (e.g. from manual yaml edits).
    config_data: dict[str, Any] = {"name": agent_name}
    new_description = description if description is not None else existing_cfg.description
    config_data["description"] = new_description
    if description is not None and description != existing_cfg.description:
        updated_fields.append("description")

    new_model = model if model is not None else existing_cfg.model
    if new_model is not None:
        config_data["model"] = new_model
    if model is not None and model != existing_cfg.model:
        updated_fields.append("model")

    new_tool_groups = tool_groups if tool_groups is not None else existing_cfg.tool_groups
    if new_tool_groups is not None:
        config_data["tool_groups"] = new_tool_groups
    if tool_groups is not None and tool_groups != existing_cfg.tool_groups:
        updated_fields.append("tool_groups")

    new_skills = skills if skills is not None else existing_cfg.skills
    if new_skills is not None:
        config_data["skills"] = new_skills
    if skills is not None and skills != existing_cfg.skills:
        updated_fields.append("skills")

    config_changed = bool({"description", "model", "tool_groups", "skills"} & set(updated_fields))

    if soul is not None:
        updated_fields.append("soul")

    if not updated_fields:
        return Command(update={"messages": [ToolMessage(content=f"No changes applied to agent '{agent_name}'. The provided values matched the existing config.", tool_call_id=tool_call_id)]})

    unresolved: list[str] = []
    if service is not None:
        try:
            saved, unresolved = await service.update_authoring_bundle(
                owner_user_id=user_id,
                slug=agent_name,
                description=description,
                soul_markdown=soul,
                model_name=model,
                tool_groups=tool_groups,
                skill_names=skills,
            )
            if saved is None:
                return _err(f"Agent '{agent_name}' does not exist in the database.")
        except Exception as e:
            logger.error("[update_agent] DB draft update failed for '%s': %s", agent_name, e)
            return _err(f"Failed to update agent '{agent_name}' in the database: {e}.")
    else:
        from deerflow.tools.builtins.agent_file_transaction import AgentFileTransaction

        files = AgentFileTransaction(agent_dir)
        try:
            if config_changed:
                files.stage_text(
                    agent_dir / "config.yaml",
                    yaml.dump(config_data, default_flow_style=False, allow_unicode=True, sort_keys=False),
                )
            if soul is not None:
                files.stage_text(agent_dir / "SOUL.md", soul)
            files.apply()
            files.finish()
        except Exception as e:
            files.rollback()
            logger.error("[update_agent] Failed to update agent '%s': %s", agent_name, e, exc_info=True)
            return _err(f"Failed to update agent '{agent_name}': {e}")

    logger.info("[update_agent] Updated agent '%s' (user=%s) fields: %s", agent_name, user_id, updated_fields)

    success_msg = f"Agent '{agent_name}' updated successfully. Changed: {', '.join(updated_fields)}. The new configuration takes effect on the next user turn."
    if unresolved:
        success_msg += f" Warning: skills not available and were excluded from the draft: {', '.join(unresolved)}."
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=success_msg,
                    tool_call_id=tool_call_id,
                )
            ]
        }
    )


# Sync entry for StructuredTool._run() (DeerFlowClient.stream() path).
def _update_agent_sync(runtime, soul=None, description=None, skills=None, tool_groups=None, model=None):
    from deerflow.persistence.engine import get_session_factory
    from deerflow.tools.builtins.setup_agent_tool import _run_async

    if get_session_factory() is not None:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Error: update_agent cannot run through the synchronous embedded client while database persistence is enabled. Use the async Gateway client.",
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )
    return _run_async(update_agent.coroutine(runtime=runtime, soul=soul, description=description, skills=skills, tool_groups=tool_groups, model=model))


update_agent.func = _update_agent_sync
