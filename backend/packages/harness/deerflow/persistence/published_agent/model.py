"""ORM models for the published-agent control plane.

This module owns the *stable identity* (``published_agents``) and the *mutable
draft* (``agent_drafts``) tables described in design doc §7.1 / §7.2 and dev
plan F1.1. A draft is a 1:1 row with its agent; the skill selection and
connector grants live in normalised sub-tables so ownership / capability checks
stay queryable.

All models follow the repository conventions established by
``persistence/api_key`` and ``persistence/connector``: ``Mapped`` /
``mapped_column`` typing, JSON columns suffixed ``_json`` (renamed by the
repository layer), ``DateTime(timezone=True)`` with a UTC ``_now()`` helper,
and no SQLAlchemy ``relationship()`` (joins are manual).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class PublishedAgentRow(Base):
    """A stable, externally visible agent identity owned by one platform user.

    The ``id`` is the value exposed in URLs / API paths; ``current_release_id``
    is an internal pointer that is NULL until the owner publishes for the first
    time. Status transitions: ``draft`` -> ``published`` -> (``suspended`` |
    ``archived``).
    """

    __tablename__ = "published_agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    avatar_ref: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    current_release_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("owner_user_id", "slug", name="uq_published_agents_owner_slug"),)


class AgentDraftRow(Base):
    """The owner-editable mutable state of an agent (1:1 with ``published_agents``).

    ``revision`` is an optimistic-concurrency counter: every successful update
    bumps it and callers must supply the value they last read. JSON columns
    (``tool_groups_json`` / ``quota_overrides_json``) are renamed to their plain
    forms by ``AgentDraftRepository``.
    """

    __tablename__ = "agent_drafts"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    soul_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_name: Mapped[str | None] = mapped_column(String(128))
    tool_groups_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quota_overrides_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False)


class AgentDraftSkillRow(Base):
    """A skill selected in a draft. ``(agent_id, skill_name)`` is unique.

    ``source`` records whether the skill is a platform ``public`` skill or a
    ``private`` skill owned by the agent owner. The classifier is informational;
    authorization is re-checked at publish time.
    """

    __tablename__ = "agent_draft_skills"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="public")

    __table_args__ = (Index("ix_agent_draft_skills_agent", "agent_id"),)


class AgentDraftConnectorGrantRow(Base):
    """A connector capability granted to a draft.

    The composite key is ``(agent_id, connector_instance_id, capability)``: a
    single connector instance may be granted at capability granularity (e.g.
    ``database.query`` but not ``database.write``). The row only references the
    connector instance id; it never embeds secrets.
    """

    __tablename__ = "agent_draft_connector_grants"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_instance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    capability: Mapped[str] = mapped_column(String(80), primary_key=True)

    __table_args__ = (Index("ix_agent_draft_connector_grants_agent", "agent_id"),)
