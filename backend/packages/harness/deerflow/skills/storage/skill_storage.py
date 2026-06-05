"""Abstract SkillStorage base class with template-method flows."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path

from deerflow.skills.types import SKILL_MD_FILE, Skill, SkillCategory  # noqa: F401

logger = logging.getLogger(__name__)

_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillStorage(ABC):
    """Abstract base for skill storage backends.

    Subclasses implement a small set of storage-medium-specific atomic
    operations; this base class provides final template-method flows
    (load_skills, history serialisation, path helpers, validation) that
    compose them with protocol-level helpers.
    """

    def __init__(self, container_path: str = "/mnt/skills") -> None:
        self._container_root = container_path

    # ------------------------------------------------------------------
    # Static protocol helpers (not storage-specific)
    # ------------------------------------------------------------------

    @staticmethod
    def validate_skill_name(name: str) -> str:
        """Validate and normalise a skill name; return the normalised form."""
        normalized = name.strip()
        if not _SKILL_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("Skill name must be hyphen-case using lowercase letters, digits, and hyphens only.")
        if len(normalized) > 64:
            raise ValueError("Skill name must be 64 characters or fewer.")
        return normalized

    @staticmethod
    def validate_relative_path(relative_path: str, base_dir: Path) -> Path:
        """Validate *relative_path* against *base_dir* and return the resolved target.

        Checks that *relative_path* is non-empty, then joins it with *base_dir*
        and resolves the result (following symlinks).  Raises ``ValueError`` if
        the resolved target does not lie within *base_dir*.
        """
        if not relative_path:
            raise ValueError("relative_path must not be empty.")
        resolved_base = base_dir.resolve()
        target = (resolved_base / relative_path).resolve()
        try:
            target.relative_to(resolved_base)
        except ValueError as exc:
            raise ValueError("relative_path must resolve within the skill directory.") from exc
        return target

    @staticmethod
    def validate_skill_markdown_content(name: str, content: str) -> None:
        """Validate SKILL.md content: parse frontmatter and check name matches."""
        import tempfile

        from deerflow.skills.validation import _validate_skill_frontmatter

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_skill_dir = Path(tmp_dir) / SkillStorage.validate_skill_name(name)
            temp_skill_dir.mkdir(parents=True, exist_ok=True)
            (temp_skill_dir / SKILL_MD_FILE).write_text(content, encoding="utf-8")
            is_valid, message, parsed_name = _validate_skill_frontmatter(temp_skill_dir)
            if not is_valid:
                raise ValueError(message)
            if parsed_name != name:
                raise ValueError(f"Frontmatter name '{parsed_name}' must match requested skill name '{name}'.")

    _ALLOWED_SUPPORT_SUBDIRS = frozenset({"skills", "references", "templates", "scripts", "assets"})

    def _resolve_safe_support_path(
        self,
        name: str,
        relative_path: str,
        *,
        require_filename: bool,
    ) -> Path:
        """Validate and return the resolved absolute path under an allowed support directory."""
        skill_dir = self.get_custom_skill_dir(self.validate_skill_name(name)).resolve()
        normalized_path = relative_path.replace("\\", "/").strip("/")
        if not normalized_path:
            raise ValueError("Path must not be empty.")
        if require_filename and (normalized_path.endswith("/") or normalized_path in self._ALLOWED_SUPPORT_SUBDIRS):
            raise ValueError("Supporting file path must include a filename.")
        relative = Path(normalized_path)
        if relative.is_absolute():
            raise ValueError("Path must be relative.")
        if any(part in {"..", ""} for part in relative.parts):
            raise ValueError("Path must not contain parent-directory traversal.")
        top_level = relative.parts[0] if relative.parts else ""
        if top_level not in self._ALLOWED_SUPPORT_SUBDIRS:
            raise ValueError(
                f"Paths must live under one of: {', '.join(sorted(self._ALLOWED_SUPPORT_SUBDIRS))}.",
            )
        target = (skill_dir / relative).resolve()
        allowed_root = (skill_dir / top_level).resolve()
        try:
            target.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError("Path must stay within the selected support directory.") from exc
        return target

    def ensure_safe_support_path(self, name: str, relative_path: str) -> Path:
        """Validate and return the resolved absolute path for a support file."""
        normalized_path = relative_path.replace("\\", "/").strip("/")
        if not normalized_path or normalized_path.endswith("/"):
            raise ValueError("Supporting file path must include a filename.")
        return self._resolve_safe_support_path(name, normalized_path, require_filename=True)

    def ensure_safe_support_dir_path(self, name: str, relative_path: str) -> Path:
        """Validate and return the resolved absolute path for a support directory."""
        return self._resolve_safe_support_path(name, relative_path, require_filename=False)

    # ------------------------------------------------------------------
    # Abstract atomic operations (storage-medium specific)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_skills_root_path(self) -> Path:
        """Absolute host path to the skills root, used for sandbox mounts.

        Origin: ``deerflow.skills.loader.get_skills_root_path``.
        """

    @abstractmethod
    def _iter_skill_files(self) -> Iterable[tuple[SkillCategory, Path, Path]]:
        """Yield ``(category, category_root, skill_md_path)`` for every SKILL.md.

        Origin: extracted from directory-walk logic inside
        ``deerflow.skills.loader.load_skills``.
        """

    @abstractmethod
    def read_custom_skill(self, name: str) -> str:
        """Read SKILL.md content for a custom skill.

        Origin: ``deerflow.skills.manager.read_custom_skill_content``.
        """

    @abstractmethod
    def read_public_skill(self, name: str) -> str:
        """Read SKILL.md content for a public skill."""

    @abstractmethod
    def write_custom_skill(self, name: str, relative_path: str, content: str) -> None:
        """Atomically write a text file under ``custom/<name>/<relative_path>``.

        Origin: ``deerflow.skills.manager.atomic_write``.
        """

    @abstractmethod
    async def ainstall_skill_from_archive(self, archive_path: str | Path, *, skip_security_scan: bool = False) -> dict:
        """Async install of a skill from a ``.skill`` ZIP archive.

        Origin: ``deerflow.skills.installer.ainstall_skill_from_archive``.
        """

    def install_skill_from_archive(self, archive_path: str | Path, *, skip_security_scan: bool = False) -> dict:
        """Sync wrapper — delegates to :meth:`ainstall_skill_from_archive`."""
        from deerflow.skills.installer import _run_async_install

        return _run_async_install(self.ainstall_skill_from_archive(archive_path, skip_security_scan=skip_security_scan))

    @abstractmethod
    def delete_custom_skill(self, name: str, *, history_meta: dict | None = None) -> None:
        """Delete a custom skill (validation + optional history + directory removal).

        Origin: ``app.gateway.routers.skills.delete_custom_skill`` + ``skill_manage_tool``.
        """

    @abstractmethod
    def custom_skill_exists(self, name: str) -> bool:
        """Origin: ``deerflow.skills.manager.custom_skill_exists``."""

    @abstractmethod
    def public_skill_exists(self, name: str) -> bool:
        """Origin: ``deerflow.skills.manager.public_skill_exists``."""

    @abstractmethod
    def append_history(self, name: str, record: dict) -> None:
        """Append a JSONL history entry for ``name``.

        Origin: ``deerflow.skills.manager.append_history``.
        """

    @abstractmethod
    def read_history(self, name: str) -> list[dict]:
        """Return all history records for ``name``, oldest first.

        Origin: ``deerflow.skills.manager.read_history``.
        """

    # ------------------------------------------------------------------
    # Versions (protocol-level; storage-medium specific persistence)
    # ------------------------------------------------------------------

    @abstractmethod
    def create_skill_version(
        self,
        name: str,
        *,
        action: str,
        author: str,
        message: str | None = None,
        thread_id: str | None = None,
    ) -> dict:
        """Create an immutable snapshot version for a custom skill.

        Implementations should return the created version metadata.
        """

    @abstractmethod
    def list_skill_versions(self, name: str) -> list[dict]:
        """List versions for a custom skill, newest-first."""

    @abstractmethod
    def list_skill_version_files(self, name: str, seq: int) -> list[dict[str, str | int | None]]:
        """List files for a specific version snapshot, same shape as list_custom_skill_files()."""

    @abstractmethod
    def read_skill_version_file(self, name: str, seq: int, relative_path: str) -> str:
        """Read a text file from a specific version snapshot."""

    @abstractmethod
    def restore_skill_version(
        self,
        name: str,
        seq: int,
        *,
        author: str,
        thread_id: str | None = None,
    ) -> dict:
        """Restore a custom skill directory from a version snapshot.

        Implementations should be careful to preserve history/version metadata stores.
        """

    # ------------------------------------------------------------------
    # Concrete path helpers (layout is part of the SKILL.md protocol)
    # ------------------------------------------------------------------

    def get_container_root(self) -> str:
        """Origin: ``deerflow.config.skills_config.SkillsConfig.container_path`` accessor."""
        return self._container_root

    def get_custom_skill_dir(self, name: str) -> Path:
        """Path to ``custom/<name>``. Does not create the directory.

        Origin: ``deerflow.skills.manager.get_custom_skill_dir``.
        """
        normalized_name = self.validate_skill_name(name)
        return self.get_skills_root_path() / SkillCategory.CUSTOM.value / normalized_name

    def get_custom_skill_file(self, name: str) -> Path:
        """Path to ``custom/<name>/SKILL.md``.

        Origin: ``deerflow.skills.manager.get_custom_skill_file``.
        """
        normalized_name = self.validate_skill_name(name)
        return self.get_custom_skill_dir(normalized_name) / SKILL_MD_FILE

    def get_skill_history_file(self, name: str) -> Path:
        """Path to ``custom/.history/<name>.jsonl``. Does not create parents.

        Origin: ``deerflow.skills.manager.get_skill_history_file``.
        """
        normalized_name = self.validate_skill_name(name)
        return self.get_skills_root_path() / SkillCategory.CUSTOM.value / ".history" / f"{normalized_name}.jsonl"

    def get_skill_versions_dir(self, name: str) -> Path:
        """Path to ``custom/.versions/<name>``. Does not create parents."""
        normalized_name = self.validate_skill_name(name)
        return self.get_skills_root_path() / SkillCategory.CUSTOM.value / ".versions" / normalized_name

    def get_skill_versions_index_file(self, name: str) -> Path:
        """Path to ``custom/.versions/<name>/index.jsonl``. Does not create parents."""
        return self.get_skill_versions_dir(name) / "index.jsonl"

    @staticmethod
    def validate_skill_version_seq(seq: int) -> int:
        """Validate a version sequence id and return the normalised int."""
        if not isinstance(seq, int):
            raise ValueError("Version seq must be an integer.")
        if seq <= 0:
            raise ValueError("Version seq must be >= 1.")
        return seq

    # ------------------------------------------------------------------
    # Final template-method flows
    # ------------------------------------------------------------------

    def load_skills(self, *, enabled_only: bool = False) -> list[Skill]:
        """Discover all skills, merge enabled state, sort and optionally filter.

        Origin: ``deerflow.skills.loader.load_skills``.
        """
        from deerflow.skills.parser import parse_skill_file

        skills_by_name: dict[str, Skill] = {}
        for category, category_root, md_path in self._iter_skill_files():
            skill = parse_skill_file(
                md_path,
                category=category,
                relative_path=md_path.parent.relative_to(category_root),
            )
            if skill:
                skills_by_name[skill.name] = skill

        skills = list(skills_by_name.values())

        # Merge enabled state from extensions config (re-read every call so
        # changes made by another process are picked up immediately).
        try:
            from deerflow.config.extensions_config import ExtensionsConfig

            extensions_config = ExtensionsConfig.from_file()
            for skill in skills:
                skill.enabled = extensions_config.is_skill_enabled(skill.name, skill.category)
        except Exception as e:
            logger.warning("Failed to load extensions config: %s", e)

        if enabled_only:
            skills = [s for s in skills if s.enabled]

        skills.sort(key=lambda s: s.name)
        return skills

    def ensure_custom_skill_is_editable(self, name: str) -> None:
        """Origin: ``deerflow.skills.manager.ensure_custom_skill_is_editable``."""
        normalized_name = self.validate_skill_name(name)
        if self.custom_skill_exists(normalized_name):
            return
        # A custom directory may already contain in-progress files before SKILL.md exists.
        if self.get_custom_skill_dir(normalized_name).exists():
            return
        if self.public_skill_exists(normalized_name):
            # Bootstrap an empty custom override directory; SKILL.md may arrive in the same apply batch.
            self.get_custom_skill_dir(normalized_name).mkdir(parents=True, exist_ok=True)
            return
        raise FileNotFoundError(f"Custom skill '{name}' not found.")

    def list_custom_skill_files(self, name: str) -> list[dict[str, str | int | None]]:
        """Return a flat list of files and directories under custom/<name>/.

        Each entry contains ``path`` (relative), ``type`` (``file`` | ``directory``),
        and ``size`` (bytes, files only).
        """
        import os

        normalized_name = self.validate_skill_name(name)
        skill_dir = self.get_custom_skill_dir(normalized_name)
        if not skill_dir.exists():
            raise FileNotFoundError(f"Custom skill '{normalized_name}' not found.")

        entries: list[dict[str, str | int | None]] = []
        for current_root, dir_names, file_names in os.walk(skill_dir, followlinks=False):
            dir_names[:] = sorted(d for d in dir_names if not d.startswith("."))
            rel_root = Path(current_root).relative_to(skill_dir)
            for directory in sorted(dir_names):
                rel_path = str(rel_root / directory) if rel_root.parts else directory
                entries.append({"path": rel_path.replace("\\", "/"), "type": "directory", "size": None})
            for filename in sorted(file_names):
                if filename.startswith("."):
                    continue
                rel_path = str(rel_root / filename) if rel_root.parts else filename
                file_path = Path(current_root) / filename
                entries.append(
                    {
                        "path": rel_path.replace("\\", "/"),
                        "type": "file",
                        "size": file_path.stat().st_size,
                    }
                )
        return entries

    _BINARY_READ_SUFFIXES = frozenset(
        {".skill", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"},
    )

    def read_custom_skill_file(self, name: str, relative_path: str) -> str:
        """Read a text file under custom/<name>/ by relative path."""
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        normalized_path = relative_path.replace("\\", "/").lstrip("/")
        if normalized_path == SKILL_MD_FILE:
            return self.read_custom_skill(name)
        if Path(normalized_path).suffix.lower() in self._BINARY_READ_SUFFIXES:
            raise ValueError(f"Binary file '{normalized_path}' cannot be read as text.")
        target = self.validate_relative_path(normalized_path, self.get_custom_skill_dir(normalized_name))
        if not target.is_file():
            raise FileNotFoundError(f"File '{normalized_path}' not found for skill '{normalized_name}'.")
        return target.read_text(encoding="utf-8")

    def mkdir_custom_skill_directory(self, name: str, relative_path: str) -> None:
        """Create a directory under an allowed support subdirectory."""
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        target = self.ensure_safe_support_dir_path(normalized_name, relative_path)
        target.mkdir(parents=True, exist_ok=True)

    def write_custom_skill_bytes(self, name: str, relative_path: str, data: bytes) -> None:
        """Write raw bytes to a support file path."""
        import tempfile

        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        target = self.ensure_safe_support_path(normalized_name, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(target.parent)) as tmp_file:
            tmp_file.write(data)
            tmp_path = Path(tmp_file.name)
        tmp_path.replace(target)

    def delete_custom_skill_file(self, name: str, relative_path: str) -> str:
        """Delete a support file under custom/<name>/ and return its previous content."""
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        normalized_path = relative_path.replace("\\", "/").strip("/")
        if normalized_path == SKILL_MD_FILE:
            raise ValueError("SKILL.md cannot be deleted.")
        target = self.ensure_safe_support_path(normalized_name, normalized_path)
        if not target.is_file():
            raise FileNotFoundError(
                f"File '{normalized_path}' not found for skill '{normalized_name}'.",
            )
        prev_content = target.read_text(encoding="utf-8")
        target.unlink()
        return prev_content
