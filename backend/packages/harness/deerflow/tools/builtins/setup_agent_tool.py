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


def _persist_draft_identity(
    *,
    owner_user_id: str,
    slug: str,
    display_name: str,
    soul_markdown: str,
    description: str,
    skills: list[str] | None,
) -> None:
    """Schedule an async mirror of a just-created agent into the DB draft store.

    The LangChain ``@tool`` function is synchronous and runs inside the main
    event loop. We schedule the async mirror via ``loop.create_task`` so it runs
    on the SAME loop that owns the global ``AsyncEngine`` — no cross-loop sharing
    (eighth-review Important-1). The tool reports the synchronously-determined
    outcome (unresolved skills, filesystem success) in the ToolMessage; the DB
    mirror is best-effort and logged if it fails.
    """
    try:
        from deerflow.publishing.factory import build_draft_service

        service = build_draft_service()
        if service is None:
            return
        import asyncio

        async def _run() -> None:
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
                        agent_id = next(
                            (a["id"] for a in await service.list_agents(owner_user_id) if a["slug"] == slug),
                            None,
                        )
                        await service.update_agent_meta(
                            agent_id,
                            owner_user_id=owner_user_id,
                            display_name=display_name,
                            description=description,
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning("[agent_creator] Failed to sync identity metadata for '%s'", slug)
                agents = await service.list_agents(owner_user_id)
                agent = next((a for a in agents if a["slug"] == slug), None)
                if agent is None:
                    return
                draft = await service.get_draft(agent["id"], owner_user_id=owner_user_id)
                if draft is None:
                    return
                try:
                    await service.update_draft_bundle(
                        agent["id"],
                        owner_user_id=owner_user_id,
                        revision=draft["revision"],
                        soul_markdown=soul_markdown,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("[agent_creator] Failed to mirror soul for '%s'", slug)
                    return
                if skills is not None:
                    refreshed = await service.get_draft(agent["id"], owner_user_id=owner_user_id)
                    if refreshed is None:
                        return
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
                        logger.warning("[agent_creator] Failed to mirror skills for '%s'", slug)
            except Exception:  # noqa: BLE001
                logger.warning("[agent_creator] Draft mirror failed for '%s'", slug)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_run())
        except RuntimeError:
            asyncio.run(_run())
    except Exception:
        logger.debug("draft persistence unavailable; filesystem write remains source of truth", exc_info=True)


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

        # Schedule the DB mirror on the same event loop (no cross-loop engine
        # sharing). The mirror is best-effort; failures are logged.
        if agent_name:
            _persist_draft_identity(
                owner_user_id=resolve_runtime_user_id(runtime),
                slug=agent_name,
                display_name=agent_name,
                soul_markdown=soul,
                description=description,
                skills=skills,
            )

        logger.info(f"[agent_creator] Created agent '{agent_name}' at {agent_dir}")
        # The ToolMessage reflects the filesystem write (synchronous, already
        # done) and the synchronously-determined skill availability. The DB
        # draft mirror runs asynchronously; its failures are logged but do not
        # block the tool response (eighth-review Important-1: no cross-loop
        # engine sharing, no false success claims about the DB write).
        success_msg = f"Agent '{agent_name}' created successfully!"
        if agent_name and skills:
            try:
                from deerflow.publishing.factory import build_draft_service

                svc = build_draft_service()
                if svc is not None:
                    _sel, unresolved = svc.filter_selectable_skills(skills, owner_user_id=resolve_runtime_user_id(runtime))
                    if unresolved:
                        success_msg += f" Warning: skills not available and were excluded from the draft: {', '.join(unresolved)}."
            except Exception:
                pass
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
