"""Metadata registration smoke tests for agent_releases / skill_revisions.

Locks in the F1.2 structural invariants from the dev plan: releases are
immutable (no ``updated_at``), ``(agent_id, release_no)`` is unique, and the
connector-grant sub-table carries only an instance reference (no secrets).
"""

from __future__ import annotations

from deerflow.persistence.agent_release.model import (
    AgentReleaseConnectorGrantRow,
    AgentReleaseRow,
    AgentReleaseSkillRow,
)
from deerflow.persistence.base import Base
from deerflow.persistence.skill_revision.model import SkillRevisionRow


def test_release_tables_are_registered():
    assert AgentReleaseRow.__tablename__ in Base.metadata.tables
    assert AgentReleaseSkillRow.__tablename__ in Base.metadata.tables
    assert AgentReleaseConnectorGrantRow.__tablename__ in Base.metadata.tables
    assert SkillRevisionRow.__tablename__ in Base.metadata.tables


def test_agent_releases_has_no_updated_at():
    """Releases are write-once: no ``updated_at`` column may exist."""
    table = Base.metadata.tables[AgentReleaseRow.__tablename__]
    assert "updated_at" not in table.columns


def test_agent_releases_agent_release_no_unique():
    table = Base.metadata.tables[AgentReleaseRow.__tablename__]
    names = {c.name for c in table.constraints}
    assert "uq_agent_releases_agent_release_no" in names


def test_agent_release_skills_composite_primary_key():
    table = Base.metadata.tables[AgentReleaseSkillRow.__tablename__]
    assert {c.name for c in table.primary_key.columns} == {"release_id", "skill_revision_id"}


def test_agent_release_connector_grants_composite_primary_key_and_no_secrets():
    table = Base.metadata.tables[AgentReleaseConnectorGrantRow.__tablename__]
    assert {c.name for c in table.primary_key.columns} == {"release_id", "connector_instance_id", "capability"}
    column_names = set(table.columns.keys())
    forbidden = {"secret", "secret_hash", "credential_ref", "api_key", "token"}
    assert not (column_names & forbidden)


def test_skill_revisions_content_dedup_unique():
    """(skill_name, owner_user_id, content_checksum) is unique so identical content reuses a revision."""
    table = Base.metadata.tables[SkillRevisionRow.__tablename__]
    names = {c.name for c in table.constraints}
    assert "uq_skill_revisions_content" in names


def test_agent_release_row_expected_columns():
    table = Base.metadata.tables[AgentReleaseRow.__tablename__]
    expected = {
        "id",
        "agent_id",
        "release_no",
        "agent_markdown",
        "soul_markdown",
        "model_name",
        "tool_groups_json",
        "quota_overrides_json",
        "manifest_checksum",
        "created_by",
        "created_at",
    }
    assert expected <= set(table.columns.keys())


def test_skill_revision_row_expected_columns():
    table = Base.metadata.tables[SkillRevisionRow.__tablename__]
    expected = {
        "id",
        "skill_name",
        "owner_user_id",
        "visibility",
        "content_checksum",
        "content_ref",
        "declared_connector_caps_json",
        "created_at",
    }
    assert expected <= set(table.columns.keys())
