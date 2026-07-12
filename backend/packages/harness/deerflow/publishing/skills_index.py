"""Adapters that bridge existing platform subsystems onto the publishing
``SkillsIndex`` / ``ConnectorRepoLike`` protocols.

These live in the harness (not ``app``) because the skill storage and connector
service are themselves harness modules; the Gateway router instantiates them
per request and hands them to ``DraftService``.
"""

from __future__ import annotations

from typing import Any

from deerflow.publishing.draft_service import SkillsIndex
from deerflow.skills.types import SkillCategory


class StorageSkillsIndex:
    """``SkillsIndex`` backed by a ``SkillStorage`` (the existing loader).

    A skill is selectable by an owner if it is either public, or a custom skill
    owned by that user (per the ``.owners`` metadata the storage already
    maintains).
    """

    def __init__(self, storage: Any, *, owner_user_id: str) -> None:
        self._storage = storage
        self._owner_user_id = owner_user_id
        self._index: dict[str, dict[str, str | None]] | None = None

    def _ensure_index(self) -> dict[str, dict[str, str | None]]:
        if self._index is None:
            skills = self._storage.load_skills(enabled_only=False)
            index: dict[str, dict[str, str | None]] = {}
            for skill in skills:
                owner: str | None = None
                if skill.category == SkillCategory.CUSTOM:
                    owner = self._storage._read_custom_skill_owner(skill.skill_dir)  # noqa: SLF001
                index[skill.name] = {
                    "visibility": "public" if skill.category == SkillCategory.PUBLIC else "private",
                    "owner": owner,
                }
            self._index = index
        return self._index

    def is_selectable_by(self, name: str, owner_user_id: str) -> bool:
        info = self._ensure_index().get(name)
        if info is None:
            return False
        if info["visibility"] == "public":
            return True
        # Private skill: only selectable by its owner. Skills with no recorded
        # owner are legacy/global and treated as non-selectable for isolation.
        return bool(info["owner"]) and info["owner"] == owner_user_id


class ConnectorServiceRepo:
    """Adapter exposing ``ConnectorService.get_connector`` as ``get_instance``.

    ``DraftService`` only needs to know whether a connector instance belongs to
    the agent owner; this adapter returns a plain dict (or ``None``) to match
    the ``ConnectorRepoLike`` protocol.
    """

    def __init__(self, connector_service: Any) -> None:
        self._service = connector_service

    async def get_instance(self, connector_id: str, *, owner_id: Any = ...) -> dict[str, Any] | None:
        try:
            instance = await self._service.get_connector(connector_id, owner_id=owner_id)
        except Exception:
            return None
        if instance is None:
            return None
        data = instance.model_dump() if hasattr(instance, "model_dump") else dict(instance)
        return data
