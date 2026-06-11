from types import SimpleNamespace

import pytest

from app.gateway.external import skill_policy
from app.gateway.external.errors import ExternalAPIError
from deerflow.config.agents_config import AgentConfig


def _skill(name):
    return SimpleNamespace(name=name)


def test_available_skills_are_intersection_of_enabled_key_and_agent(monkeypatch):
    storage = SimpleNamespace(load_skills=lambda enabled_only: [_skill("sales"), _skill("customer"), _skill("hidden")])
    monkeypatch.setattr(skill_policy, "get_or_new_skill_storage", lambda app_config: storage)
    monkeypatch.setattr(skill_policy, "load_agent_config", lambda name: AgentConfig(name=name, skills=["sales", "hidden"]))
    result = skill_policy.available_external_skills(
        app_config=object(),
        allowed_skills=["sales", "customer"],
        agent_id="business-agent",
    )
    assert [skill.name for skill in result] == ["sales"]


def test_missing_or_unauthorized_skill_has_same_not_found_error(monkeypatch):
    storage = SimpleNamespace(load_skills=lambda enabled_only: [_skill("sales")])
    monkeypatch.setattr(skill_policy, "get_or_new_skill_storage", lambda app_config: storage)
    with pytest.raises(ExternalAPIError) as exc:
        skill_policy.require_external_skill(
            app_config=object(),
            allowed_skills=[],
            agent_id="lead_agent",
            skill_name="sales",
        )
    assert exc.value.status_code == 404
    assert exc.value.code == "skill_not_available"
