"""Tests for publish-time draft validation (F1.5).

The validator implements the 8 rules in design doc §8.2. Each rule gets at least
one passing and one failing case, and the suite confirms that *all* violations
are returned at once (aggregate, not fail-fast) so the Studio UI can render them
together.
"""

from __future__ import annotations

import pytest

from deerflow.publishing.validation import (
    MAX_INSTRUCTION_BYTES,
    PLATFORM_QUOTA_DEFAULTS,
    PublishViolation,
    validate_draft_for_publish,
)


def _draft(**overrides):
    base = {
        "agent_id": "pa_1",
        "agent_markdown": "# Agent",
        "soul_markdown": "",
        "model_name": "gpt-x",
        "tool_groups": ["web"],
        "quota_overrides": {},
        "skills": [{"skill_name": "reporting", "source": "public"}],
        "connector_grants": [{"connector_instance_id": "conn_1", "capability": "database.query"}],
    }
    base.update(overrides)
    return base


def _skills_index(skills):
    """name -> {visibility, owner, caps (list[str])}"""

    class _Idx:
        def __init__(self, mapping):
            self.mapping = mapping

        def is_selectable_by(self, name, owner_user_id):
            return name in self.mapping

        def get(self, name):
            return self.mapping.get(name)

    return _Idx(skills)


def _connector_repo(owners, capabilities=None):
    capabilities = capabilities or {connector_id: {"database.query"} for connector_id in owners}

    class _Repo:
        def __init__(self, owners, capabilities):
            self.owners = owners
            self.capabilities = capabilities

        def get_instance(self, connector_id, *, owner_id=...):
            if connector_id in self.owners and (owner_id is ... or self.owners[connector_id] == owner_id):
                return {
                    "id": connector_id,
                    "owner_id": self.owners[connector_id],
                    "status": "active",
                    "supported_capabilities": tuple(sorted(self.capabilities.get(connector_id, set()))),
                }
            return None

    return _Repo(owners, capabilities)


@pytest.fixture()
def collaborators():
    return {
        "owner_user_id": "user-a",
        "skills_index": _skills_index({"reporting": {"visibility": "public", "owner": None, "caps": ["database.query"]}}),
        "connector_repo": _connector_repo({"conn_1": "user-a"}),
        "model_index": {"gpt-x"},
        "tool_group_whitelist": {"web", "file:read"},
        "platform_quota": PLATFORM_QUOTA_DEFAULTS,
    }


def codes(violations: list[PublishViolation]) -> set[str]:
    return {v.code for v in violations}


# ---------------------------------------------------------------------------
# Rule 1: at least one instruction file non-empty
# ---------------------------------------------------------------------------


def test_rule1_empty_instructions_violation(collaborators):
    violations = validate_draft_for_publish(_draft(agent_markdown="   ", soul_markdown="\n"), **collaborators)
    assert "EMPTY_INSTRUCTIONS" in codes(violations)


def test_rule1_only_soul_ok(collaborators):
    violations = validate_draft_for_publish(_draft(agent_markdown="", soul_markdown="# Soul"), **collaborators)
    assert "EMPTY_INSTRUCTIONS" not in codes(violations)


# ---------------------------------------------------------------------------
# Rule 2: instruction size limit
# ---------------------------------------------------------------------------


def test_rule2_instruction_too_large(collaborators):
    big = "x" * (MAX_INSTRUCTION_BYTES + 1)
    violations = validate_draft_for_publish(_draft(agent_markdown=big), **collaborators)
    assert "INSTRUCTION_TOO_LARGE" in codes(violations)


def test_rule2_instruction_within_limit_ok(collaborators):
    ok = "x" * 100
    violations = validate_draft_for_publish(_draft(agent_markdown=ok), **collaborators)
    assert "INSTRUCTION_TOO_LARGE" not in codes(violations)


# ---------------------------------------------------------------------------
# Rule 3: model available to owner
# ---------------------------------------------------------------------------


def test_rule3_model_not_available(collaborators):
    violations = validate_draft_for_publish(_draft(model_name="unknown-model"), **collaborators)
    assert "MODEL_NOT_AVAILABLE" in codes(violations)


def test_rule3_model_available_ok(collaborators):
    violations = validate_draft_for_publish(_draft(model_name="gpt-x"), **collaborators)
    assert "MODEL_NOT_AVAILABLE" not in codes(violations)


# ---------------------------------------------------------------------------
# Rule 4: skills exist / enabled / owned
# ---------------------------------------------------------------------------


def test_rule4_skill_not_found(collaborators):
    violations = validate_draft_for_publish(_draft(skills=[{"skill_name": "ghost", "source": "public"}]), **collaborators)
    assert "SKILL_NOT_FOUND" in codes(violations)


def test_rule4_skill_found_ok(collaborators):
    violations = validate_draft_for_publish(_draft(skills=[{"skill_name": "reporting", "source": "public"}]), **collaborators)
    assert "SKILL_NOT_FOUND" not in codes(violations)


# ---------------------------------------------------------------------------
# Rule 5: skill-declared connector capabilities must be granted
# ---------------------------------------------------------------------------


def test_rule5_skill_capability_not_granted(collaborators):
    # Skill requires "database.write" but only "database.query" is granted.
    collaborators["skills_index"] = _skills_index({"reporting": {"visibility": "public", "owner": None, "caps": ["database.write"]}})
    violations = validate_draft_for_publish(_draft(), **collaborators)
    assert "CONNECTOR_NOT_GRANTED" in codes(violations)


def test_rule5_skill_capability_granted_ok(collaborators):
    violations = validate_draft_for_publish(_draft(), **collaborators)
    assert "CONNECTOR_NOT_GRANTED" not in codes(violations)


# ---------------------------------------------------------------------------
# Rule 6: connector instances belong to owner and are valid
# ---------------------------------------------------------------------------


def test_rule6_connector_not_owned(collaborators):
    collaborators["connector_repo"] = _connector_repo({})  # conn_1 belongs to nobody
    violations = validate_draft_for_publish(_draft(), **collaborators)
    assert "CONNECTOR_NOT_OWNED" in codes(violations)


def test_rule6_connector_owned_ok(collaborators):
    violations = validate_draft_for_publish(_draft(), **collaborators)
    assert "CONNECTOR_NOT_OWNED" not in codes(violations)


def test_rule6_connector_type_must_support_granted_capability(collaborators):
    collaborators["connector_repo"] = _connector_repo({"conn_1": "user-a"}, {"conn_1": {"mail.send"}})
    violations = validate_draft_for_publish(_draft(), **collaborators)
    assert "CONNECTOR_CAPABILITY_UNSUPPORTED" in codes(violations)
    assert "CONNECTOR_NOT_GRANTED" in codes(violations)


# ---------------------------------------------------------------------------
# Rule 7: tool_groups in whitelist
# ---------------------------------------------------------------------------


def test_rule7_tool_group_not_whitelisted(collaborators):
    violations = validate_draft_for_publish(_draft(tool_groups=["web", "dangerous"]), **collaborators)
    assert "TOOL_GROUP_UNKNOWN" in codes(violations)


def test_rule7_tool_group_whitelisted_ok(collaborators):
    violations = validate_draft_for_publish(_draft(tool_groups=["web", "file:read"]), **collaborators)
    assert "TOOL_GROUP_UNKNOWN" not in codes(violations)


# ---------------------------------------------------------------------------
# Rule 8: quota overrides within platform hard limits
# ---------------------------------------------------------------------------


def test_rule8_quota_exceeds_platform(collaborators):
    violations = validate_draft_for_publish(
        _draft(quota_overrides={"max_concurrent_runs": PLATFORM_QUOTA_DEFAULTS["max_concurrent_runs"] + 1}),
        **collaborators,
    )
    assert "QUOTA_EXCEEDS_PLATFORM" in codes(violations)


def test_rule8_quota_within_platform_ok(collaborators):
    violations = validate_draft_for_publish(_draft(quota_overrides={"max_concurrent_runs": 1}), **collaborators)
    assert "QUOTA_EXCEEDS_PLATFORM" not in codes(violations)


# ---------------------------------------------------------------------------
# Aggregation: multiple violations returned at once
# ---------------------------------------------------------------------------


def test_multiple_violations_aggregated(collaborators):
    violations = validate_draft_for_publish(
        _draft(agent_markdown="", soul_markdown="", model_name="ghost", tool_groups=["dangerous"]),
        **collaborators,
    )
    found = codes(violations)
    assert "EMPTY_INSTRUCTIONS" in found
    assert "MODEL_NOT_AVAILABLE" in found
    assert "TOOL_GROUP_UNKNOWN" in found


def test_clean_draft_has_no_violations(collaborators):
    violations = validate_draft_for_publish(_draft(), **collaborators)
    assert violations == []
