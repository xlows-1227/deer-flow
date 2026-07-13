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


def _run_mirror_sync(coro_factory):
    """Run an async mirror function synchronously in a dedicated thread+loop.

    LangChain ``@tool`` functions are synchronous and run inside an already-
    running event loop. ``loop.create_task`` would be fire-and-forget — the tool
    returns before the DB write completes (seventh-review Important-2). Instead,
    we run the coroutine in a separate thread with its own loop and block until
    it finishes, so the tool can report the actual outcome.

    ``coro_factory`` is a zero-arg callable returning the coroutine to run.
    Returns the coroutine's result, or ``None`` on failure (logged).
    """
    import asyncio
    import concurrent.futures

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: asyncio.run(coro_factory()))
            return future.result(timeout=10)
    except Exception:
        logger.debug("draft mirror failed; filesystem write remains source of truth", exc_info=True)
        return None


def _persist_draft_identity(
    *,
    owner_user_id: str,
    slug: str,
    display_name: str,
    soul_markdown: str,
    description: str,
    skills: list[str] | None,
) -> dict | None:
    """Mirror a just-created agent into the published-agent DB.

    Returns ``{"succeeded": bool, "unresolved": list[str]}`` or ``None`` when
    persistence is unavailable (seventh-review Important-2). The caller waits
    for this to finish before reporting success, so the ToolMessage accurately
    reflects whether the draft was written and which skills were excluded.
    """
    try:
        from deerflow.publishing.factory import build_draft_service

        service = build_draft_service()
        if service is None:
            return None
    except Exception:
        logger.debug("draft persistence unavailable; filesystem write remains source of truth", exc_info=True)
        return None

    async def _run() -> dict:
        unresolved: list[str] = []
        succeeded = True
        try:
            try:
                await service.create_agent(
                    owner_user_id=owner_user_id,
                    slug=slug,
                    display_name=display_name,
                    description=description or None,
                )
            except ValueError:
                try:
                    agent_id = next((a["id"] for a in await service.list_agents(owner_user_id) if a["slug"] == slug), None)
                    await service.update_agent_meta(agent_id, owner_user_id=owner_user_id, display_name=display_name, description=description)
                except Exception:  # noqa: BLE001
                    pass
            agents = await service.list_agents(owner_user_id)
            agent = next((a for a in agents if a["slug"] == slug), None)
            if agent is None:
                return {"succeeded": False, "unresolved": []}
            draft = await service.get_draft(agent["id"], owner_user_id=owner_user_id)
            if draft is None:
                return {"succeeded": False, "unresolved": []}
            try:
                await service.update_draft_bundle(
                    agent["id"],
                    owner_user_id=owner_user_id,
                    revision=draft["revision"],
                    soul_markdown=soul_markdown,
                )
            except Exception:  # noqa: BLE001
                succeeded = False
            if skills is not None:
                refreshed = await service.get_draft(agent["id"], owner_user_id=owner_user_id)
                if refreshed is not None:
                    selectable, unresolved = service.filter_selectable_skills(skills, owner_user_id=owner_user_id)
                    if unresolved:
                        logger.warning("[agent_creator] Skills not selectable (dropped): %s", unresolved)
                    skill_entries = [{"skill_name": s, "source": "public"} for s in selectable]
                    try:
                        await service.update_draft_bundle(
                            agent["id"],
                            owner_user_id=owner_user_id,
                            revision=refreshed["revision"],
                            skills=skill_entries,
                        )
                    except Exception:  # noqa: BLE001
                        succeeded = False
        except Exception:  # noqa: BLE001
            succeeded = False
        return {"succeeded": succeeded, "unresolved": unresolved}

    return _run_mirror_sync(_run)


@tool(parse_docstring=True)
def setup_agent(
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

    agent_name: str | None = runtime.context.get("agent_name") if runtime.context else None
    agent_dir = None
    is_new_dir = False

    try:
        agent_name = validate_agent_name(agent_name)
        paths = get_paths()
        if agent_name:
            user_id = resolve_runtime_user_id(runtime)
            agent_dir = paths.user_agent_dir(user_id, agent_name)
        else:
            agent_dir = paths.base_dir
        is_new_dir = not agent_dir.exists()
        agent_dir.mkdir(parents=True, exist_ok=True)

        if agent_name:
            config_data: dict = {"name": agent_name}
            if description:
                config_data["description"] = description
            if skills is not None:
                config_data["skills"] = skills
            config_file = agent_dir / "config.yaml"
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

        soul_file = agent_dir / "SOUL.md"
        soul_file.write_text(soul, encoding="utf-8")

        # Mirror into the published-agent draft store, waiting for completion
        # so the ToolMessage reflects the actual outcome (seventh-review
        # Important-2).
        mirror_result = None
        if agent_name:
            mirror_result = _persist_draft_identity(
                owner_user_id=resolve_runtime_user_id(runtime),
                slug=agent_name,
                display_name=agent_name,
                soul_markdown=soul,
                description=description,
                skills=skills,
            )

        logger.info(f"[agent_creator] Created agent '{agent_name}' at {agent_dir}")
        success_msg = f"Agent '{agent_name}' created successfully!"
        if mirror_result is not None:
            if not mirror_result.get("succeeded", True):
                success_msg += " Warning: some draft fields could not be mirrored to the database."
            unresolved = mirror_result.get("unresolved", [])
            if unresolved:
                success_msg += f" Warning: skills not available and were excluded: {', '.join(unresolved)}."
        return Command(
            update={
                "created_agent_name": agent_name,
                "messages": [ToolMessage(content=success_msg, tool_call_id=runtime.tool_call_id)],
            }
        )

    except Exception as e:
        import shutil

        if agent_name and is_new_dir and agent_dir is not None and agent_dir.exists():
            shutil.rmtree(agent_dir)
        logger.error(f"[agent_creator] Failed to create agent '{agent_name}': {e}", exc_info=True)
        return Command(update={"messages": [ToolMessage(content=f"Error: {e}", tool_call_id=runtime.tool_call_id)]})
