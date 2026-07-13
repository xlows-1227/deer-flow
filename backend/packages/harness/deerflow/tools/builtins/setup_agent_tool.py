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


async def _mirror_draft_identity(
    *,
    owner_user_id: str,
    slug: str,
    display_name: str,
    soul_markdown: str,
    description: str,
    skills: list[str] | None,
) -> dict:
    """Write the agent identity + draft via DraftService on the current event loop.

    Returns ``{"succeeded": bool, "unresolved": list[str], "error": str | None}``.
    The caller (an async LangChain tool) directly ``await``s this — no executor,
    no cross-loop engine sharing (ninth-review Important-1).
    """
    result: dict = {"succeeded": False, "unresolved": [], "error": None}
    try:
        from deerflow.publishing.factory import build_draft_service

        service = build_draft_service()
        if service is None:
            # Persistence not configured (e.g. CLI-only). This is acceptable —
            # the filesystem write remains the source of truth and no "DB-only"
            # invariant is violated because there IS no DB.
            result["succeeded"] = True
            result["error"] = "unavailable"
            return result
    except Exception as exc:
        result["error"] = str(exc)
        return result

    try:
        try:
            await service.create_agent(
                owner_user_id=owner_user_id,
                slug=slug,
                display_name=display_name,
                description=description or None,
            )
        except ValueError:
            # Duplicate slug — sync identity metadata.
            agent_id = next(
                (a["id"] for a in await service.list_agents(owner_user_id) if a["slug"] == slug),
                None,
            )
            if agent_id:
                await service.update_agent_meta(
                    agent_id,
                    owner_user_id=owner_user_id,
                    display_name=display_name,
                    description=description,
                )
        agents = await service.list_agents(owner_user_id)
        agent = next((a for a in agents if a["slug"] == slug), None)
        if agent is None:
            result["error"] = "agent not found after create"
            return result
        draft = await service.get_draft(agent["id"], owner_user_id=owner_user_id)
        if draft is None:
            result["error"] = "draft not found"
            return result
        await service.update_draft_bundle(
            agent["id"],
            owner_user_id=owner_user_id,
            revision=draft["revision"],
            soul_markdown=soul_markdown,
        )
        unresolved: list[str] = []
        if skills is not None:
            refreshed = await service.get_draft(agent["id"], owner_user_id=owner_user_id)
            if refreshed is not None:
                selectable, unresolved = service.filter_selectable_skills(skills, owner_user_id=owner_user_id)
                if unresolved:
                    logger.warning("[agent_creator] Skills not selectable (dropped): %s", unresolved)
                skill_entries = [{"skill_name": s, "source": "public"} for s in selectable]
                await service.update_draft_bundle(
                    agent["id"],
                    owner_user_id=owner_user_id,
                    revision=refreshed["revision"],
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

        # Write to DB draft store (DraftService) as the authoritative source.
        # This is an async tool, so we directly await on the same event loop
        # that owns the global AsyncEngine — no executor, no cross-loop sharing
        # (ninth-review Important-1). DB success is required: if it fails and
        # persistence IS configured, we clean up the filesystem and report
        # failure — no "filesystem-only" agent (ninth-review Important-2).
        unresolved: list[str] = []
        if agent_name:
            mirror = await _mirror_draft_identity(
                owner_user_id=resolve_runtime_user_id(runtime),
                slug=agent_name,
                display_name=agent_name,
                soul_markdown=soul,
                description=description,
                skills=skills,
            )
            unresolved = mirror.get("unresolved", [])
            if not mirror["succeeded"] and mirror.get("error") != "unavailable":
                # DB write failed and persistence IS configured — clean up and fail.
                import shutil

                if is_new_dir and agent_dir is not None and agent_dir.exists():
                    shutil.rmtree(agent_dir)
                err = mirror.get("error", "unknown")
                logger.error("[agent_creator] DB draft creation failed for '%s': %s", agent_name, err)
                return Command(update={"messages": [ToolMessage(content=f"Error: failed to create agent '{agent_name}' in the database: {err}", tool_call_id=runtime.tool_call_id)]})

        logger.info(f"[agent_creator] Created agent '{agent_name}' at {agent_dir}")
        success_msg = f"Agent '{agent_name}' created successfully!"
        if unresolved:
            success_msg += f" Warning: skills not available and were excluded from the draft: {', '.join(unresolved)}."
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
