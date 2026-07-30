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

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from deerflow.skills.parser import parse_allowed_tools, parse_connector_requirements
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

    def _load_index(self) -> dict[str, dict[str, Any]]:
        # Load all skills (including disabled) so ``get`` can still report
        # metadata, but record the enabled flag so ``is_selectable_by`` rejects
        # disabled skills (rereview Important-3).
        skills = self._storage.load_skills(enabled_only=False)
        index: dict[str, dict[str, Any]] = {}
        for skill in skills:
            owner: str | None = None
            if skill.category == SkillCategory.CUSTOM:
                owner = self._storage._read_custom_skill_owner(skill.skill_dir)  # noqa: SLF001
            caps = [req.capability for req in skill.connector_requirements or []]
            index[skill.name] = {
                "visibility": "public" if skill.category == SkillCategory.PUBLIC else "private",
                "owner": owner,
                # Older SkillStorage implementations and lightweight adapter
                # fixtures predate the optional localized catalog fields.
                "display_name": getattr(skill, "display_name", None),
                "description": getattr(skill, "description", None),
                "description_zh": getattr(skill, "description_zh", None),
                "caps": caps,
                "skill_dir": skill.skill_dir,
                "enabled": bool(skill.enabled),
            }
        return index

    def _ensure_index(self) -> dict[str, dict[str, Any]]:
        if self._index is None:
            self._index = self._load_index()
        return self._index

    @staticmethod
    def _is_info_selectable(info: dict[str, Any] | None, owner_user_id: str) -> bool:
        if info is None or not info.get("enabled", True):
            return False
        if info.get("visibility") == "public":
            return True
        return bool(info.get("owner")) and info.get("owner") == owner_user_id

    def is_selectable_by(self, name: str, owner_user_id: str) -> bool:
        info = self._ensure_index().get(name)
        return self._is_info_selectable(info, owner_user_id)

    def get(self, name: str) -> dict[str, Any] | None:
        """Return authoritative metadata for ``name`` or ``None`` if unknown.

        The dict carries ``visibility``, ``owner``, ``caps`` (declared connector
        capabilities), and ``skill_dir``. Callers must use these values rather
        than any client-supplied ``source``.
        """
        return self._ensure_index().get(name)

    def list_selectable_by(self, owner_user_id: str) -> list[dict[str, Any]]:
        """Return the owner's current enabled skill set in stable order."""
        result: list[dict[str, Any]] = []
        for name in sorted(self._ensure_index()):
            if not self.is_selectable_by(name, owner_user_id):
                continue
            info = self._ensure_index()[name]
            result.append(
                {
                    "skill_name": name,
                    "source": ("private" if info.get("visibility") == "private" else "public"),
                    "display_name": info.get("display_name"),
                    "description": info.get("description"),
                    "description_zh": info.get("description_zh"),
                    "declared_connector_caps": list(info.get("caps") or []),
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
        initial_index = self._ensure_index()
        names = [name for name in sorted(initial_index) if self._is_info_selectable(initial_index.get(name), owner_user_id)] if skill_names is None else list(dict.fromkeys(skill_names))

        # The storage does not expose one atomic metadata+tree snapshot. Use a
        # fail-closed bracketing protocol instead: capture every file tree twice
        # and bracket those reads with fresh authoritative metadata reads. A
        # concurrent edit to the tree, enabled state, visibility, owner or path
        # rejects that Skill for this publish attempt.
        first_files = {name: self._capture_files(initial_index.get(name)) for name in names}
        middle_index = self._load_index()
        second_files = {name: self._capture_files(middle_index.get(name)) for name in names}
        final_index = self._load_index()
        self._index = final_index

        return {
            name: self._snapshot_from_consistent_capture(
                name,
                owner_user_id=owner_user_id,
                initial_info=initial_index.get(name),
                middle_info=middle_index.get(name),
                final_info=final_index.get(name),
                first_files=first_files[name],
                second_files=second_files[name],
            )
            for name in names
        }

    @staticmethod
    def _metadata_fingerprint(info: dict[str, Any] | None) -> tuple[Any, ...] | None:
        if info is None:
            return None
        skill_dir = info.get("skill_dir")
        return (
            info.get("visibility"),
            info.get("owner"),
            bool(info.get("enabled", True)),
            str(Path(skill_dir).resolve()) if skill_dir else None,
        )

    @staticmethod
    def _capture_files(info: dict[str, Any] | None) -> dict[str, bytes] | None:
        if info is None or not info.get("skill_dir"):
            return None
        skill_dir = Path(info["skill_dir"])
        if not skill_dir.is_dir():
            return None
        files: dict[str, bytes] = {}
        try:
            for path in skill_dir.rglob("*"):
                relative = path.relative_to(skill_dir)
                if path.is_file() and not any(part.startswith(".") for part in relative.parts):
                    files[relative.as_posix()] = path.read_bytes()
        except OSError:
            return None
        return files if files.get("SKILL.md") else None

    @staticmethod
    def _caps_from_captured_skill_md(skill_name: str, content: bytes) -> tuple[str, ...] | None:
        """Parse connector requirements from the exact bytes being pinned."""
        skill_file = Path(skill_name) / "SKILL.md"
        try:
            text = content.decode("utf-8")
            match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n", text, re.DOTALL)
            if match is None:
                return None
            metadata = yaml.safe_load(match.group(1))
            if not isinstance(metadata, dict):
                return None
            parsed_name = metadata.get("name")
            description = metadata.get("description")
            if not isinstance(parsed_name, str) or parsed_name.strip() != skill_name:
                return None
            if not isinstance(description, str) or not description.strip():
                return None
            # Validate the same structured fields as the normal Skill parser so
            # a concurrently written malformed SKILL.md cannot be published.
            parse_allowed_tools(metadata.get("allowed-tools"), skill_file)
            requirements = parse_connector_requirements(metadata.get("requires"), skill_file) or []
        except (UnicodeDecodeError, ValueError, yaml.YAMLError):
            return None
        return tuple(requirement.capability for requirement in requirements)

    def _snapshot_from_consistent_capture(
        self,
        name: str,
        *,
        owner_user_id: str,
        initial_info: dict[str, Any] | None,
        middle_info: dict[str, Any] | None,
        final_info: dict[str, Any] | None,
        first_files: dict[str, bytes] | None,
        second_files: dict[str, bytes] | None,
    ) -> SkillPublishSnapshot | None:
        fingerprint = self._metadata_fingerprint(initial_info)
        if fingerprint is None or fingerprint != self._metadata_fingerprint(middle_info) or fingerprint != self._metadata_fingerprint(final_info):
            return None
        if first_files is None or first_files != second_files:
            return None
        if not self._is_info_selectable(final_info, owner_user_id):
            return None
        assert final_info is not None
        visibility = final_info.get("visibility")
        owner = final_info.get("owner")
        if visibility not in {"public", "private"}:
            return None
        if visibility == "private" and (not isinstance(owner, str) or owner != owner_user_id):
            return None
        caps = self._caps_from_captured_skill_md(name, first_files["SKILL.md"])
        if caps is None:
            return None
        return SkillPublishSnapshot(
            skill_name=name,
            source=visibility,
            visibility=visibility,
            owner_user_id=owner if visibility == "private" else None,
            declared_connector_caps=caps,
            files=tuple(sorted(first_files.items())),
        )

    def _resolve_publish_snapshot(
        self,
        name: str,
        owner_user_id: str,
    ) -> SkillPublishSnapshot | None:
        return self.resolve_publish_snapshots([name], owner_user_id).get(name)

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

    The returned plain dict includes the immutable, authority-derived
    ``supported_capabilities`` tuple from the current Connector type. Unknown,
    disabled or malformed instances/types return ``None``.
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
            type_definition = await self._service.get_connector_type(connector_type)
        except Exception:
            # Config / registry / unknown-type failure — fail closed (do NOT grant).
            return None
        type_data = type_definition.model_dump() if hasattr(type_definition, "model_dump") else dict(type_definition)
        raw_capabilities = type_data.get("capabilities")
        if not isinstance(raw_capabilities, (list, tuple, set, frozenset)):
            return None
        capabilities: list[str] = []
        for capability in raw_capabilities:
            if not isinstance(capability, str) or not capability.strip():
                return None
            capabilities.append(capability.strip())
        # Tuple makes the authority-derived capability set immutable to callers.
        data["supported_capabilities"] = tuple(dict.fromkeys(capabilities))
        return data
