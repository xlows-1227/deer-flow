"""ORM models for agent_releases and their immutable sub-tables.

A release is a write-once snapshot of a draft at publish time (design doc
§7.3–§7.5). Once inserted, no code path updates a release row — rollbacks work
by repointing ``published_agents.current_release_id``, never by mutating
history. That immutability is reinforced structurally: the repository in
``sql.py`` exposes no update mutators, and the table has no ``updated_at``
column.

The release references pinned ``skill_revisions`` via the
``agent_release_skills`` join table, and records connector capability grants
via ``agent_release_connector_grants`` — the latter stores only a connector
instance id, never any secret material.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class AgentReleaseRow(Base):
    """An immutable, owner-visible publish snapshot.

    ``release_no`` is per-agent, monotonically incrementing (1, 2, 3 …) and
    unique within an agent. ``manifest_checksum`` is the canonicalised
    manifest SHA-256 so two releases with identical content are still distinct
    rows but comparable. There is deliberately no ``updated_at``.
    """

    __tablename__ = "agent_releases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    release_no: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    soul_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_name: Mapped[str | None] = mapped_column(String(128))
    tool_groups_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quota_overrides_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest_checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (UniqueConstraint("agent_id", "release_no", name="uq_agent_releases_agent_release_no"),)


class AgentReleaseSkillRow(Base):
    """Join row pinning a release to a specific, immutable skill revision."""

    __tablename__ = "agent_release_skills"

    release_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_releases.id"), primary_key=True)
    skill_revision_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class AgentReleaseConnectorGrantRow(Base):
    """An immutable record of a connector capability granted to a release.

    Only the connector instance id is referenced; the row never embeds secrets.
    At run time the resolver intersects this grant with the connector's current
    status, so revoking a capability takes immediate effect even though the
    release itself is immutable.
    """

    __tablename__ = "agent_release_connector_grants"

    release_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_releases.id"), primary_key=True)
    connector_instance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    capability: Mapped[str] = mapped_column(String(80), primary_key=True)
