"""Metadata registration smoke tests for published_agents / agent_drafts tables.

Mirrors the style of ``tests/test_connectors_models.py``: these tests prove the
model module imports cleanly and that every table lands in ``Base.metadata``
(which Alembic autogenerate reads). They also lock in the structural invariants
called out in the dev plan F1.1 acceptance criteria (owner+slug uniqueness,
status default value, composite primary keys on the sub-tables).
"""

from __future__ import annotations

from deerflow.persistence.base import Base
from deerflow.persistence.published_agent.model import (
    AgentDraftConnectorGrantRow,
    AgentDraftRow,
    AgentDraftSkillRow,
    PublishedAgentRow,
)


def test_published_agent_tables_are_registered():
    assert PublishedAgentRow.__tablename__ in Base.metadata.tables
    assert AgentDraftRow.__tablename__ in Base.metadata.tables
    assert AgentDraftSkillRow.__tablename__ in Base.metadata.tables
    assert AgentDraftConnectorGrantRow.__tablename__ in Base.metadata.tables


def test_published_agents_owner_slug_unique_constraint():
    """The (owner_user_id, slug) pair must carry a named unique constraint."""
    table = Base.metadata.tables[PublishedAgentRow.__tablename__]
    constraint_names = {c.name for c in table.constraints}
    assert "uq_published_agents_owner_slug" in constraint_names


def test_published_agents_status_default_is_draft():
    """New agents default to ``draft`` until the owner explicitly publishes."""
    table = Base.metadata.tables[PublishedAgentRow.__tablename__]
    status_col = table.columns["status"]
    assert status_col.default is not None
    # The default renders as a constant scalar in both SQLite and PG.
    assert status_col.default.arg == "draft"


def test_published_agents_owner_and_status_indexed():
    table = Base.metadata.tables[PublishedAgentRow.__tablename__]
    indexed = {col.name for col in table.columns if col.index}
    assert "owner_user_id" in indexed
    assert "status" in indexed


def test_published_agents_current_release_is_nullable():
    """``current_release_id`` is an internal pointer that is NULL until first publish."""
    table = Base.metadata.tables[PublishedAgentRow.__tablename__]
    assert table.columns["current_release_id"].nullable is True


def test_agent_drafts_primary_key_is_agent_id():
    """Drafts are 1:1 with their agent via the agent_id primary key."""
    table = Base.metadata.tables[AgentDraftRow.__tablename__]
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["agent_id"]


def test_agent_drafts_revision_defaults_to_one():
    """Optimistic concurrency starts at revision 1."""
    table = Base.metadata.tables[AgentDraftRow.__tablename__]
    revision_col = table.columns["revision"]
    assert revision_col.default is not None
    assert revision_col.default.arg == 1


def test_agent_draft_skills_composite_primary_key():
    table = Base.metadata.tables[AgentDraftSkillRow.__tablename__]
    pk_cols = {c.name for c in table.primary_key.columns}
    assert pk_cols == {"agent_id", "skill_name"}


def test_agent_draft_connector_grants_composite_primary_key():
    table = Base.metadata.tables[AgentDraftConnectorGrantRow.__tablename__]
    pk_cols = {c.name for c in table.primary_key.columns}
    assert pk_cols == {"agent_id", "connector_instance_id", "capability"}


def test_agent_draft_connector_grants_has_no_secret_fields():
    """The grant sub-table only references a connector instance id; it never embeds secrets."""
    table = Base.metadata.tables[AgentDraftConnectorGrantRow.__tablename__]
    column_names = set(table.columns.keys())
    forbidden = {"secret", "secret_hash", "credential_ref", "api_key", "token"}
    assert not (column_names & forbidden)


def test_published_agent_row_has_expected_columns():
    table = Base.metadata.tables[PublishedAgentRow.__tablename__]
    expected = {
        "id",
        "owner_user_id",
        "slug",
        "display_name",
        "description",
        "avatar_ref",
        "status",
        "current_release_id",
        "created_at",
        "updated_at",
    }
    assert expected <= set(table.columns.keys())


def test_agent_draft_row_has_expected_columns():
    table = Base.metadata.tables[AgentDraftRow.__tablename__]
    expected = {
        "agent_id",
        "agent_markdown",
        "soul_markdown",
        "model_name",
        "tool_groups_json",
        "quota_overrides_json",
        "revision",
        "updated_at",
        "updated_by",
    }
    assert expected <= set(table.columns.keys())


def test_id_and_fk_columns_fit_generated_ids():
    """ID/FK columns must be wide enough for the generated ids (pa_/rel_/skr_ + 32 hex = up to 36 chars).

    SQLite does not enforce VARCHAR length, so a too-narrow column passes tests
    but fails under PostgreSQL. This guard keeps the schema PostgreSQL-safe.
    """
    # Longest generated id is "rel_" + 32 hex chars = 36.
    min_required = len("rel_") + 32
    for tablename, columns in (
        (PublishedAgentRow.__tablename__, ("id", "current_release_id")),
        (AgentDraftRow.__tablename__, ("agent_id",)),
        (AgentDraftSkillRow.__tablename__, ("agent_id",)),
        (AgentDraftConnectorGrantRow.__tablename__, ("agent_id",)),
    ):
        table = Base.metadata.tables[tablename]
        for name in columns:
            length = table.columns[name].type.length
            assert length >= min_required, f"{tablename}.{name} width {length} < {min_required}"
