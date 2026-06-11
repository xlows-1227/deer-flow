"""Authorization policy for Skills exposed through External API."""

from __future__ import annotations

from app.gateway.external.errors import ExternalAPIError
from deerflow.config.agents_config import load_agent_config, validate_agent_name
from deerflow.skills.storage import get_or_new_skill_storage


def require_external_agent(agent_id: str):
    if agent_id in {"lead_agent", "lead-agent"}:
        return None
    try:
        return load_agent_config(validate_agent_name(agent_id))
    except (FileNotFoundError, ValueError) as exc:
        raise ExternalAPIError(
            code="agent_not_available",
            message="The requested agent is not available.",
            status_code=404,
        ) from exc


def available_external_skills(*, app_config, allowed_skills: list[str], agent_id: str = "lead_agent"):
    storage = get_or_new_skill_storage(app_config=app_config)
    enabled = {skill.name: skill for skill in storage.load_skills(enabled_only=True)}
    allowed = set(allowed_skills)
    if agent_id in {"lead_agent", "lead-agent"}:
        agent_allowed = None
    else:
        agent = require_external_agent(agent_id)
        agent_allowed = set(agent.skills) if agent and agent.skills is not None else None
    names = set(enabled) & allowed
    if agent_allowed is not None:
        names &= agent_allowed
    return [enabled[name] for name in sorted(names)]


def require_external_skill(*, app_config, allowed_skills: list[str], agent_id: str, skill_name: str):
    match = next(
        (
            skill
            for skill in available_external_skills(
                app_config=app_config,
                allowed_skills=allowed_skills,
                agent_id=agent_id,
            )
            if skill.name == skill_name
        ),
        None,
    )
    if match is None:
        raise ExternalAPIError(
            code="skill_not_available",
            message="The requested skill is not available.",
            status_code=404,
        )
    return match
