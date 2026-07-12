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
    """Best-effort mirror of a just-created agent into the published-agent DB.

    The conversational ``setup_agent`` flow and the structured Studio editor must
    land on the same draft source of truth (design §16.3, code-review
    Important-3). When persistence is available we create the agent identity +
    draft and write the soul/description/skills fields through the same
    ``DraftService`` so there is no "filesystem-only" agent with missing fields.
    Failures are logged and swallowed: the filesystem write is the compatibility
    fallback during the migration window.
    """
    try:
        from deerflow.publishing.factory import build_draft_service

        service = build_draft_service()
        if service is None:
            return
        import asyncio

        async def _run() -> None:
            # Create the identity if it does not exist; on a duplicate slug,
            # sync identity metadata + draft rather than returning early
            # (third-review Important-5).
            try:
                await service.create_agent(
                    owner_user_id=owner_user_id,
                    slug=slug,
                    display_name=display_name,
                    description=description or None,
                )
            except ValueError:
                # Duplicate slug — still sync the identity metadata in case the
                # filesystem write changed display_name/description.
                try:
                    await service.update_agent_meta(
                        next((a["id"] for a in await service.list_agents(owner_user_id) if a["slug"] == slug), None),
                        owner_user_id=owner_user_id,
                        display_name=display_name,
                        description=description or None,
                    )
                except Exception:  # noqa: BLE001
                    pass
            agents = await service.list_agents(owner_user_id)
            agent = next((a for a in agents if a["slug"] == slug), None)
            if agent is None:
                return
            draft = await service.get_draft(agent["id"], owner_user_id=owner_user_id)
            if draft is None:
                return
            # Mirror soul first on its own so an unresolvable legacy skill name
            # cannot block the soul write.
            try:
                await service.update_draft_bundle(
                    agent["id"],
                    owner_user_id=owner_user_id,
                    revision=draft["revision"],
                    soul_markdown=soul_markdown,
                )
            except Exception:  # noqa: BLE001
                return
            if skills is None:
                return
            refreshed = await service.get_draft(agent["id"], owner_user_id=owner_user_id)
            if refreshed is None:
                return
            # Attempt to mirror the full skills list. If validation rejects any
            # legacy name, the soul is already saved and we leave skills
            # unchanged (best-effort; third-review Important-5).
            skill_entries = [{"skill_name": s, "source": "public"} for s in skills]
            try:
                await service.update_draft_bundle(
                    agent["id"],
                    owner_user_id=owner_user_id,
                    revision=refreshed["revision"],
                    skills=skill_entries,
                )
            except Exception:  # noqa: BLE001
                # Skill validation rejected at least one name; keep soul, skip skills.
                return

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
            # Custom agents are persisted under the current user's bucket so
            # different users do not see each other's agents.
            user_id = resolve_runtime_user_id(runtime)
            agent_dir = paths.user_agent_dir(user_id, agent_name)
        else:
            # Default agent (no agent_name): SOUL.md lives at the global base dir.
            agent_dir = paths.base_dir
        is_new_dir = not agent_dir.exists()
        agent_dir.mkdir(parents=True, exist_ok=True)

        if agent_name:
            # If agent_name is provided, we are creating a custom agent in the agents/ directory
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

        # Mirror into the published-agent draft store (best-effort, DB-backed).
        # Keeps conversational and structured authoring consistent (design §16.3).
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
        return Command(
            update={
                "created_agent_name": agent_name,
                "messages": [ToolMessage(content=f"Agent '{agent_name}' created successfully!", tool_call_id=runtime.tool_call_id)],
            }
        )

    except Exception as e:
        import shutil

        if agent_name and is_new_dir and agent_dir is not None and agent_dir.exists():
            # Cleanup the custom agent directory only if it was newly created during this call
            shutil.rmtree(agent_dir)
        logger.error(f"[agent_creator] Failed to create agent '{agent_name}': {e}", exc_info=True)
        return Command(update={"messages": [ToolMessage(content=f"Error: {e}", tool_call_id=runtime.tool_call_id)]})
