import logging

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from deerflow.config.agents_config import validate_agent_name
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run a no-persistence coroutine for the embedded synchronous client."""
    import asyncio

    return asyncio.run(coro)


async def _setup_agent_core(
    soul: str,
    description: str,
    runtime: Runtime,
    skills: list[str] | None = None,
) -> Command:
    """Create one DB-backed draft, or use files when persistence is disabled.

    The Gateway path has a single source of truth and one SQL commit. Legacy
    per-user files are written only by the no-database CLI/embedded fallback.
    """
    agent_name: str | None = runtime.context.get("agent_name") if runtime.context else None

    try:
        agent_name = validate_agent_name(agent_name)
    except ValueError as e:
        return Command(update={"messages": [ToolMessage(content=f"Error: {e}", tool_call_id=runtime.tool_call_id)]})

    if not agent_name:
        paths = get_paths()
        (paths.base_dir / "SOUL.md").write_text(soul, encoding="utf-8")
        return Command(update={"created_agent_name": agent_name, "messages": [ToolMessage(content="Agent created successfully!", tool_call_id=runtime.tool_call_id)]})

    user_id = resolve_runtime_user_id(runtime)

    # Persistence mode writes only DraftService. Legacy files are a read-only
    # migration source and are never a second commit target.
    persistence_result = await _persist_draft_identity(
        owner_user_id=user_id,
        slug=agent_name,
        display_name=agent_name,
        soul_markdown=soul,
        description=description,
        skills=skills,
    )
    if not persistence_result["succeeded"] and persistence_result.get("error") != "unavailable":
        err = persistence_result.get("error", "unknown")
        logger.error("[agent_creator] DB draft creation failed for '%s': %s", agent_name, err)
        return Command(update={"messages": [ToolMessage(content=f"Error: failed to create agent '{agent_name}' in the database: {err}", tool_call_id=runtime.tool_call_id)]})

    db_unavailable = persistence_result.get("error") == "unavailable"
    if db_unavailable:
        paths = get_paths()
        agent_dir = paths.user_agent_dir(user_id, agent_name)
        config_data: dict = {"name": agent_name}
        if description:
            config_data["description"] = description
        if skills is not None:
            config_data["skills"] = skills

        from deerflow.tools.builtins.agent_file_transaction import AgentFileTransaction

        files = AgentFileTransaction(agent_dir)
        try:
            files.stage_text(
                agent_dir / "config.yaml",
                yaml.dump(config_data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            )
            files.stage_text(agent_dir / "SOUL.md", soul)
            files.apply()
            files.finish()
        except Exception as exc:
            files.rollback()
            logger.error("[agent_creator] Failed to create agent '%s': %s", agent_name, exc, exc_info=True)
            return Command(update={"messages": [ToolMessage(content=f"Error: {exc}", tool_call_id=runtime.tool_call_id)]})

    logger.info(f"[agent_creator] Created agent '{agent_name}'")
    success_msg = f"Agent '{agent_name}' created successfully!"
    unresolved = persistence_result.get("unresolved", [])
    if unresolved:
        success_msg += f" Warning: skills not available and were excluded from the draft: {', '.join(unresolved)}."
    return Command(update={"created_agent_name": agent_name, "messages": [ToolMessage(content=success_msg, tool_call_id=runtime.tool_call_id)]})


async def _persist_draft_identity(
    *,
    owner_user_id: str,
    slug: str,
    display_name: str,
    soul_markdown: str,
    description: str,
    skills: list[str] | None,
) -> dict:
    """Write agent identity + draft via DraftService. Returns result dict."""
    result: dict = {"succeeded": False, "unresolved": [], "error": None}
    try:
        from deerflow.publishing.factory import build_draft_service

        service = build_draft_service()
        if service is None:
            from deerflow.persistence.engine import get_session_factory

            if get_session_factory() is not None:
                result["error"] = "database persistence is enabled but DraftService is unavailable"
                return result
            result["succeeded"] = True
            result["error"] = "unavailable"
            return result
    except Exception as exc:
        result["error"] = str(exc)
        return result

    try:
        _saved, unresolved = await service.setup_authoring_bundle(
            owner_user_id=owner_user_id,
            slug=slug,
            display_name=display_name,
            description=description or None,
            soul_markdown=soul_markdown,
            skill_names=skills,
        )
        if unresolved:
            logger.warning("[agent_creator] Skills not selectable (dropped): %s", unresolved)
        result["succeeded"] = True
        result["unresolved"] = unresolved
    except Exception as exc:
        result["error"] = str(exc)
    return result


@tool(parse_docstring=True)
async def setup_agent(
    soul: str,
    description: str,
    runtime: Runtime,
    skills: list[str] | None = None,
) -> Command:
    """Setup the custom DeerFlow agent.

    Args:
        soul: Full SOUL.md content defining the agent's personality and behavior.
        description: One-line description of what the agent does.
        skills: Optional list of skill names this agent should use. None means use all enabled skills, empty list means no skills.
    """
    return await _setup_agent_core(soul, description, runtime, skills)


# Sync entry for StructuredTool._run() (DeerFlowClient.stream() path).
def _setup_agent_sync(soul, description, runtime, skills=None):
    from deerflow.persistence.engine import get_session_factory

    if get_session_factory() is not None:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Error: setup_agent cannot run through the synchronous embedded client while database persistence is enabled. Use the async Gateway client.",
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )
    return _run_async(_setup_agent_core(soul, description, runtime, skills))


setup_agent.func = _setup_agent_sync
