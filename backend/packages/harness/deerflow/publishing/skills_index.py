"""Adapters that bridge existing platform subsystems onto the publishing
``SkillsIndex`` / ``ConnectorRepoLike`` protocols.

These live in the harness (not ``app``) because the skill storage and connector
service are themselves harness modules; the Gateway router instantiates them
per request and hands them to ``DraftService``.

Visibility / ownership are resolved authoritatively from the skill storage
(``SkillCategory`` + the ``.owners`` metadata) — callers never supply a
client-chosen ``source``. This keeps ``skill_revisions`` ownership/visibility
metadata trustworthy (code-review Important-1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deerflow.skills.types import SkillCategory


@dataclass(frozen=True)
class SkillPublishSnapshot:
    """Fail-closed metadata and file bytes captured for one publish attempt."""

    skill_name: str
    source: str
    visibility: str
    owner_user_id: str | None
    declared_connector_caps: tuple[str, ...]
    files: tuple[tuple[str, bytes], ...]

    def file_map(self) -> dict[str, bytes]:
        return dict(self.files)

    def validation_info(self) -> dict[str, Any]:
        return {
            "visibility": self.visibility,
            "owner": self.owner_user_id,
            "caps": list(self.declared_connector_caps),
        }


class StorageSkillsIndex:
    """``SkillsIndex`` backed by a ``SkillStorage`` (the existing loader).

    A skill is selectable by an owner if it is either public, or a custom skill
    owned by that user (per the ``.owners`` metadata the storage already
    maintains). ``get`` returns the authoritative visibility/owner/declared-caps
    so the publish service can record a revision without trusting client input.
    """

    def __init__(self, storage: Any, *, owner_user_id: str) -> None:
        self._storage = storage
        self._owner_user_id = owner_user_id
        self._index: dict[str, dict[str, Any]] | None = None

    def _ensure_index(self) -> dict[str, dict[str, Any]]:
        if self._index is None:
            # Load all skills (including disabled) so ``get`` can still report
            # metadata, but record the enabled flag so ``is_selectable_by``
            # rejects disabled skills (rereview Important-3).
            skills = self._storage.load_skills(enabled_only=False)
            index: dict[str, dict[str, Any]] = {}
            for skill in skills:
                owner: str | None = None
                if skill.category == SkillCategory.CUSTOM:
                    owner = self._storage._read_custom_skill_owner(skill.skill_dir)  # noqa: SLF001
                caps: list[str] = []
                for req in skill.connector_requirements or []:
                    caps.append(req.capability)
                index[skill.name] = {
                    "visibility": "public" if skill.category == SkillCategory.PUBLIC else "private",
                    "owner": owner,
                    "caps": caps,
                    "skill_dir": skill.skill_dir,
                    "enabled": bool(skill.enabled),
                }
            self._index = index
        return self._index

    def is_selectable_by(self, name: str, owner_user_id: str) -> bool:
        info = self._ensure_index().get(name)
        if info is None:
            return False
        # A disabled skill is never selectable, even for its owner (rereview
        # Important-3): publishing an agent against a disabled skill would pin a
        # revision the platform has turned off.
        if not info.get("enabled", True):
            return False
        if info["visibility"] == "public":
            return True
        # Private skill: only selectable by its owner. Skills with no recorded
        # owner are legacy/global and treated as non-selectable for isolation.
        return bool(info["owner"]) and info["owner"] == owner_user_id

    def get(self, name: str) -> dict[str, Any] | None:
        """Return authoritative metadata for ``name`` or ``None`` if unknown.

        The dict carries ``visibility``, ``owner``, ``caps`` (declared connector
        capabilities), and ``skill_dir``. Callers must use these values rather
        than any client-supplied ``source``.
        """
        return self._ensure_index().get(name)

    def list_selectable_by(self, owner_user_id: str) -> list[dict[str, str]]:
        """Return the owner's current enabled skill set in stable order."""
        result: list[dict[str, str]] = []
        for name in sorted(self._ensure_index()):
            if not self.is_selectable_by(name, owner_user_id):
                continue
            info = self._ensure_index()[name]
            result.append(
                {
                    "skill_name": name,
                    "source": ("private" if info.get("visibility") == "private" else "public"),
                }
            )
        return result

    def resolve_publish_snapshots(
        self,
        skill_names: Sequence[str] | None,
        owner_user_id: str,
    ) -> dict[str, SkillPublishSnapshot | None]:
        """Resolve metadata, authorization and files from one cached index.

        ``skill_names=None`` means all currently selectable skills (inherit).
        Every candidate remains present in the result even when file capture
        fails so the publish validator can fail closed instead of silently
        dropping it.
        """
        index = self._ensure_index()
        names = [name for name in sorted(index) if self.is_selectable_by(name, owner_user_id)] if skill_names is None else list(dict.fromkeys(skill_names))
        return {name: self._resolve_publish_snapshot(name, owner_user_id) for name in names}

    def _resolve_publish_snapshot(
        self,
        name: str,
        owner_user_id: str,
    ) -> SkillPublishSnapshot | None:
        info = self._ensure_index().get(name)
        if info is None or not self.is_selectable_by(name, owner_user_id):
            return None
        visibility = info.get("visibility")
        owner = info.get("owner")
        if visibility not in {"public", "private"}:
            return None
        if visibility == "private" and (not isinstance(owner, str) or owner != owner_user_id):
            return None
        skill_dir_value = info.get("skill_dir")
        if not skill_dir_value:
            return None
        skill_dir = Path(skill_dir_value)
        if not skill_dir.is_dir():
            return None
        files: dict[str, bytes] = {}
        try:
            for path in skill_dir.rglob("*"):
                relative = path.relative_to(skill_dir)
                if path.is_file() and not any(part.startswith(".") for part in relative.parts):
                    files[relative.as_posix()] = path.read_bytes()
        except OSError:
            # A concurrent delete/rename or unreadable file invalidates the
            # whole resolution. Never publish a partial file snapshot.
            return None
        if not files.get("SKILL.md"):
            return None
        return SkillPublishSnapshot(
            skill_name=name,
            source=visibility,
            visibility=visibility,
            owner_user_id=owner if visibility == "private" else None,
            declared_connector_caps=tuple(str(cap) for cap in info.get("caps") or []),
            files=tuple(sorted(files.items())),
        )

    def files_for(self, name: str) -> dict[str, bytes]:
        """Snapshot the skill's files (SKILL.md + siblings) for content addressing.

        Returns an empty mapping if the skill is unknown — the publish service
        treats an empty snapshot as a publish-blocker via the validator's
        SKILL_NOT_FOUND rule.
        """
        info = self._ensure_index().get(name)
        if info is None:
            return {}
        skill_dir = Path(info["skill_dir"])
        files: dict[str, bytes] = {}
        if skill_dir.exists():
            for path in skill_dir.rglob("*"):
                if path.is_file() and not path.name.startswith("."):
                    rel = path.relative_to(skill_dir).as_posix()
                    files[rel] = path.read_bytes()
        return files


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
        # Strict active whitelist: only status == 'active' is grantable.
        status = str(data.get("status") or "").lower()
        if status != "active":
            return None
        connector_type = str(data.get("type") or "")
        if not connector_type:
            return None  # unknown type — fail closed
        # Fail-closed platform + registry type validation (fifth-review
        # Important-2): check platform enabled, enabled_types whitelist, and
        # verify the type is a real registered connector via the authoritative
        # ConnectorService.get_connector_type() (which raises on unknown or
        # disabled types). Any exception → fail closed.
        try:
            from deerflow.config.app_config import get_app_config

            connectors_cfg = get_app_config().connectors
            if not getattr(connectors_cfg, "enabled", True):
                return None  # platform connectors globally disabled
            enabled_types = {t.lower() for t in connectors_cfg.enabled_types}
            if enabled_types and connector_type.lower() not in enabled_types:
                return None  # type not in the platform whitelist
            # Authoritative registry check: get_connector_type raises on unknown
            # or disabled types.
            await self._service.get_connector_type(connector_type)
        except Exception:
            # Config / registry / unknown-type failure — fail closed (do NOT grant).
            return None
        return data
