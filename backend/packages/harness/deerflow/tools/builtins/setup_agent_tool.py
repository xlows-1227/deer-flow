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
    """Bridge: run an async coroutine from a sync context safely.

    When there's no running loop (sync ``DeerFlowClient.stream()`` path),
    uses ``asyncio.run()``. This is safe because ``build_draft_service()``
    returns None when no DB engine is configured (CLI), so the coroutine
    never touches the global ``AsyncEngine`` — no cross-loop sharing.

    When there IS a running loop (shouldn't happen for the sync path but
    defensive), runs in a dedicated thread to avoid blocking.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
        # Running loop exists — run in a separate thread to avoid blocking.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result(timeout=15)
    except RuntimeError:
        return asyncio.run(coro)


async def _setup_agent_core(
    soul: str,
    description: str,
    runtime: Runtime,
    skills: list[str] | None = None,
) -> Command:
    """Async core: DB-first write then filesystem, single consistency unit.

    Order: (1) DB identity + draft via DraftService (authoritative),
    (2) filesystem compat write. If DB fails and persistence IS configured,
    filesystem is never touched — no DB-only or filesystem-only partial state
    (tenth-review Important-1).
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

    # Step 1: DB write (DraftService) — the authoritative source of truth.
    mirror = await _mirror_draft_identity(
        owner_user_id=user_id,
        slug=agent_name,
        display_name=agent_name,
        soul_markdown=soul,
        description=description,
        skills=skills,
    )
    if not mirror["succeeded"] and mirror.get("error") != "unavailable":
        err = mirror.get("error", "unknown")
        logger.error("[agent_creator] DB draft creation failed for '%s': %s", agent_name, err)
        return Command(update={"messages": [ToolMessage(content=f"Error: failed to create agent '{agent_name}' in the database: {err}", tool_call_id=runtime.tool_call_id)]})

    # Step 2: Filesystem compat write (only after DB success or unavailable).
    # When DB is unavailable (CLI), filesystem IS the source of truth — errors
    # must propagate. When DB succeeded, filesystem failure is logged but not
    # fatal (DB is authoritative).
    db_unavailable = mirror.get("error") == "unavailable"
    paths = get_paths()
    agent_dir = paths.user_agent_dir(user_id, agent_name)
    is_new_dir = not agent_dir.exists()
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        config_data: dict = {"name": agent_name}
        if description:
            config_data["description"] = description
        if skills is not None:
            config_data["skills"] = skills
        with open(agent_dir / "config.yaml", "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
        (agent_dir / "SOUL.md").write_text(soul, encoding="utf-8")
    except Exception as e:
        if db_unavailable:
            # CLI mode: filesystem is the source of truth — propagate the error.
            import shutil

            if is_new_dir and agent_dir.exists():
                shutil.rmtree(agent_dir)
            logger.error(f"[agent_creator] Failed to create agent '{agent_name}': {e}", exc_info=True)
            return Command(update={"messages": [ToolMessage(content=f"Error: {e}", tool_call_id=runtime.tool_call_id)]})
        logger.error("[agent_creator] Filesystem write failed for '%s' (DB succeeded): %s", agent_name, e)

    logger.info(f"[agent_creator] Created agent '{agent_name}'")
    success_msg = f"Agent '{agent_name}' created successfully!"
    unresolved = mirror.get("unresolved", [])
    if unresolved:
        success_msg += f" Warning: skills not available and were excluded from the draft: {', '.join(unresolved)}."
    return Command(update={"created_agent_name": agent_name, "messages": [ToolMessage(content=success_msg, tool_call_id=runtime.tool_call_id)]})


async def _mirror_draft_identity(
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
            result["succeeded"] = True
            result["error"] = "unavailable"
            return result
    except Exception as exc:
        result["error"] = str(exc)
        return result

    try:
        try:
            await service.create_agent(owner_user_id=owner_user_id, slug=slug, display_name=display_name, description=description or None)
        except ValueError:
            agent_id = next((a["id"] for a in await service.list_agents(owner_user_id) if a["slug"] == slug), None)
            if agent_id:
                await service.update_agent_meta(agent_id, owner_user_id=owner_user_id, display_name=display_name, description=description)
        agents = await service.list_agents(owner_user_id)
        agent = next((a for a in agents if a["slug"] == slug), None)
        if agent is None:
            result["error"] = "agent not found after create"
            return result
        draft = await service.get_draft(agent["id"], owner_user_id=owner_user_id)
        if draft is None:
            result["error"] = "draft not found"
            return result
        unresolved: list[str] = []
        skill_entries = None
        if skills is not None:
            selectable, unresolved = service.filter_selectable_skills(skills, owner_user_id=owner_user_id)
            if unresolved:
                logger.warning("[agent_creator] Skills not selectable (dropped): %s", unresolved)
            skill_entries = [{"skill_name": s, "source": "public"} for s in selectable]
        await service.update_draft_bundle(
            agent["id"],
            owner_user_id=owner_user_id,
            revision=draft["revision"],
            soul_markdown=soul_markdown,
            skills=skill_entries,
        )
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
    return _run_async(_setup_agent_core(soul, description, runtime, skills))


setup_agent.func = _setup_agent_sync
