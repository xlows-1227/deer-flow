"""Factory that wires the publishing ``DraftService`` to live platform subsystems.

The Gateway lifespan stores a fully-wired ``DraftService`` on ``app.state``;
the conversational agent tools (``setup_agent`` / ``update_agent``) live in the
harness and cannot import ``app.*``, so they call :func:`get_draft_service` to
get the same service when running inside a process that has persistence
configured. When no database engine is initialised (e.g. CLI-only runs), the
factory returns ``None`` and the tools fall back to the legacy filesystem path.
"""

from __future__ import annotations

from deerflow.persistence.engine import get_session_factory
from deerflow.publishing.draft_service import DraftService


def build_draft_service() -> DraftService | None:
    """Construct a persistence-backed ``DraftService`` if a DB engine is live.

    Returns ``None`` when persistence is not configured, so callers can fall
    back to filesystem behaviour without raising.
    """
    sf = get_session_factory()
    if sf is None:
        return None
    from deerflow.persistence.agent_release import AgentReleaseRepository
    from deerflow.persistence.published_agent import (
        AgentDraftRepository,
        PublishedAgentRepository,
    )
    from deerflow.publishing.skills_index import ConnectorServiceRepo, StorageSkillsIndex

    # The skill storage and connector service are imported lazily so this module
    # stays cheap when no DraftService is ever requested.
    try:
        from deerflow.connectors.service import make_connector_service
        from deerflow.skills.storage import get_or_new_skill_storage
    except Exception:
        return None

    # ``StorageSkillsIndex`` is created per-call in the service wrapper below
    # because it is owner-scoped. We expose a thin proxy that constructs one on
    # demand using the resolved owner at call time.
    class _OwnerAwareSkillsIndex:
        def is_selectable_by(self, name: str, owner_user_id: str) -> bool:  # noqa: ARG002
            storage = get_or_new_skill_storage()
            return StorageSkillsIndex(storage, owner_user_id=owner_user_id).is_selectable_by(name, owner_user_id)

    return DraftService(
        published_agent_repo=PublishedAgentRepository(sf),
        draft_repo=AgentDraftRepository(sf),
        skills_index=_OwnerAwareSkillsIndex(),
        connector_repo=ConnectorServiceRepo(make_connector_service()),
    )


def build_publish_service():
    """Construct a persistence-backed ``PublishService`` if a DB engine is live.

    Returns ``None`` when persistence is not configured. Imported lazily to
    avoid a circular import with ``publish_service`` (which itself imports the
    repositories).
    """
    sf = get_session_factory()
    if sf is None:
        return None
    from deerflow.persistence.agent_release import AgentReleaseRepository
    from deerflow.persistence.published_agent import (
        AgentDraftRepository,
        PublishedAgentRepository,
    )
    from deerflow.persistence.skill_revision import SkillRevisionRepository
    from deerflow.publishing.content_store import get_content_store
    from deerflow.publishing.publish_service import PublishService

    return PublishService(
        published_agent_repo=PublishedAgentRepository(sf),
        draft_repo=AgentDraftRepository(sf),
        release_repo=AgentReleaseRepository(sf),
        skill_revision_repo=SkillRevisionRepository(sf),
        content_store=get_content_store(),
    )
