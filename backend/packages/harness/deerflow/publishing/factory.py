"""Factory that wires the publishing services to live platform subsystems.

The Gateway lifespan stores fully-wired services on ``app.state``; the
conversational agent tools (``setup_agent`` / ``update_agent``) live in the
harness and cannot import ``app.*``, so they call :func:`build_draft_service`
to get the same service when running inside a process that has persistence
configured. When no database engine is initialised (e.g. CLI-only runs), the
factories return ``None`` and callers fall back to the legacy filesystem path.
"""

from __future__ import annotations

from typing import Any

from deerflow.persistence.engine import get_session_factory


class _OwnerAwareSkillsIndex:
    """Skills index that resolves visibility/ownership on demand per owner.

    ``StorageSkillsIndex`` is owner-scoped (it reads the ``.owners`` metadata
    for custom skills), so this proxy builds one fresh per call using the
    resolved owner. It also exposes ``get``/``files_for`` so the publish
    service can read declared connector capabilities and snapshot skill files.
    """

    def __init__(self, storage: Any) -> None:
        self._storage = storage

    def _index(self, owner_user_id: str) -> Any:
        from deerflow.publishing.skills_index import StorageSkillsIndex

        return StorageSkillsIndex(self._storage, owner_user_id=owner_user_id)

    def is_selectable_by(self, name: str, owner_user_id: str) -> bool:
        return self._index(owner_user_id).is_selectable_by(name, owner_user_id)

    def get(self, name: str) -> dict[str, Any] | None:
        # Declared connector capabilities are owner-independent (they come from
        # the skill's SKILL.md frontmatter), so resolve with a placeholder owner.
        return self._index("").get(name)

    def files_for(self, name: str) -> dict[str, bytes]:
        return self._index("").files_for(name)


def _resolve_available_model_names() -> set[str]:
    """Set of model names declared in the platform config.

    Used as the publish-time model availability index. User-defined models are
    resolved separately by the publish flow (they live in a per-user table and
    require async access); config-declared models are the deterministic baseline.
    """
    try:
        from deerflow.config.app_config import get_app_config

        return {model.name for model in get_app_config().models}
    except Exception:
        return set()


def _resolve_tool_group_whitelist() -> set[str]:
    try:
        from deerflow.config.app_config import get_app_config

        return {group.name for group in get_app_config().tool_groups}
    except Exception:
        return set()


def build_draft_service():
    """Construct a persistence-backed ``DraftService`` if a DB engine is live.

    Returns ``None`` when persistence is not configured.
    """
    sf = get_session_factory()
    if sf is None:
        return None
    from deerflow.persistence.published_agent import (
        AgentDraftRepository,
        PublishedAgentRepository,
    )
    from deerflow.publishing.draft_service import DraftService
    from deerflow.publishing.skills_index import ConnectorServiceRepo

    try:
        from deerflow.connectors.service import make_connector_service
        from deerflow.skills.storage import get_or_new_skill_storage
    except Exception:
        return None

    storage = get_or_new_skill_storage()
    return DraftService(
        published_agent_repo=PublishedAgentRepository(sf),
        draft_repo=AgentDraftRepository(sf),
        skills_index=_OwnerAwareSkillsIndex(storage),
        connector_repo=ConnectorServiceRepo(make_connector_service()),
    )


def build_publish_service():
    """Construct a persistence-backed ``PublishService`` if a DB engine is live.

    Fully wired with skills index, connector adapter, available model index,
    platform tool-group whitelist, and platform quota defaults. Returns ``None``
    when persistence is not configured.
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
    from deerflow.publishing.skills_index import ConnectorServiceRepo
    from deerflow.publishing.validation import PLATFORM_QUOTA_DEFAULTS

    try:
        from deerflow.connectors.service import make_connector_service
        from deerflow.skills.storage import get_or_new_skill_storage
    except Exception:
        return None

    storage = get_or_new_skill_storage()
    return PublishService(
        published_agent_repo=PublishedAgentRepository(sf),
        draft_repo=AgentDraftRepository(sf),
        release_repo=AgentReleaseRepository(sf),
        skill_revision_repo=SkillRevisionRepository(sf),
        content_store=get_content_store(),
        skills_index=_OwnerAwareSkillsIndex(storage),
        connector_repo=ConnectorServiceRepo(make_connector_service()),
        model_index=_resolve_available_model_names(),
        tool_group_whitelist=_resolve_tool_group_whitelist(),
        platform_quota=dict(PLATFORM_QUOTA_DEFAULTS),
    )


def build_import_service():
    """Construct a persistence-backed ``AgentImportService`` if a DB engine is live.

    Returns ``None`` when persistence is not configured.
    """
    sf = get_session_factory()
    if sf is None:
        return None
    from deerflow.config.paths import get_paths
    from deerflow.persistence.published_agent import (
        AgentDraftRepository,
        PublishedAgentRepository,
    )
    from deerflow.publishing.import_service import AgentImportService

    try:
        from deerflow.skills.storage import get_or_new_skill_storage
    except Exception:
        return None

    storage = get_or_new_skill_storage()

    class _OwnerAwareImportIndex:
        """Owner-aware skills index for the import path.

        Implements both ``is_selectable_by`` and ``get`` so the import service
        derives authoritative visibility/ownership rather than defaulting every
        imported skill to ``public`` (rereview Important-4).
        """

        def _index(self, owner_user_id: str):
            from deerflow.publishing.skills_index import StorageSkillsIndex

            return StorageSkillsIndex(storage, owner_user_id=owner_user_id)

        def is_selectable_by(self, name, owner_user_id):
            return self._index(owner_user_id).is_selectable_by(name, owner_user_id)

        def get(self, name):
            # Visibility/caps are owner-independent; resolve with a placeholder.
            return self._index("").get(name)

    return AgentImportService(
        published_agent_repo=PublishedAgentRepository(sf),
        draft_repo=AgentDraftRepository(sf),
        skills_index=_OwnerAwareImportIndex(),
        base_dir=get_paths().base_dir,
    )
