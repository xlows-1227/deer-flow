"""Local-filesystem implementation of ``SkillStorage``."""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from deerflow.config.runtime_paths import resolve_path
from deerflow.skills.installer import make_skill_path_sandbox_readable
from deerflow.skills.storage.skill_storage import SKILL_MD_FILE, SkillStorage
from deerflow.skills.types import SkillCategory

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_CONTAINER_PATH = "/mnt/skills"
MAX_SKILL_VERSION_SNAPSHOTS = 5
CUSTOM_SKILL_OWNER_FILE = ".owner.json"


class LocalSkillStorage(SkillStorage):
    """Skill storage backed by the local filesystem.

    Layout::

        <root>/public/<name>/SKILL.md
        <root>/custom/<name>/SKILL.md
        <root>/custom/.history/<name>.jsonl
        <root>/custom/.versions/<name>/index.jsonl
    """

    def __init__(
        self,
        host_path: str | None = None,
        container_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
        app_config=None,
        enforce_owner_isolation: bool = False,
    ) -> None:
        super().__init__(container_path=container_path)
        self._enforce_owner_isolation = enforce_owner_isolation
        if host_path is None:
            from deerflow.config import get_app_config

            config = app_config or get_app_config()
            self._host_root: Path = config.skills.get_skills_path()
        else:
            self._host_root = resolve_path(host_path)

    # ------------------------------------------------------------------
    # Abstract operation implementations
    # ------------------------------------------------------------------

    def get_skills_root_path(self) -> Path:
        return self._host_root

    @staticmethod
    def _current_user_id() -> str:
        from deerflow.runtime.user_context import get_effective_user_id

        return get_effective_user_id()

    @staticmethod
    def _legacy_owner_file(skill_dir: Path) -> Path:
        return skill_dir / CUSTOM_SKILL_OWNER_FILE

    def _owner_file(self, skill_dir: Path) -> Path:
        return self._host_root / SkillCategory.CUSTOM.value / ".owners" / f"{skill_dir.name}.json"

    def _read_custom_skill_owner(self, skill_dir: Path) -> str | None:
        owner_file = self._owner_file(skill_dir)
        if not owner_file.exists():
            owner_file = self._legacy_owner_file(skill_dir)
        if not owner_file.exists():
            return None
        try:
            payload = json.loads(owner_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Invalid custom skill owner metadata: %s", owner_file, exc_info=True)
            return ""
        owner_id = payload.get("owner_id") if isinstance(payload, dict) else None
        return str(owner_id).strip() if owner_id else ""

    def _can_access_custom_skill_dir(self, skill_dir: Path) -> bool:
        if not self._enforce_owner_isolation:
            return True

        from deerflow.runtime.user_context import DEFAULT_USER_ID

        owner_id = self._read_custom_skill_owner(skill_dir)
        current_user_id = self._current_user_id()
        if owner_id is None:
            # Preserve CLI and pre-auth compatibility for legacy skills while
            # failing closed for authenticated users.
            return current_user_id == DEFAULT_USER_ID
        return bool(owner_id) and owner_id == current_user_id

    def _write_custom_skill_owner(self, skill_dir: Path) -> None:
        skill_dir.mkdir(parents=True, exist_ok=True)
        owner_file = self._owner_file(skill_dir)
        owner_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"owner_id": self._current_user_id()}
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(owner_file.parent),
        ) as tmp_file:
            json.dump(payload, tmp_file, ensure_ascii=False)
            tmp_file.write("\n")
            tmp_path = Path(tmp_file.name)
        tmp_path.replace(owner_file)

    def _ensure_custom_skill_owner_access(self, name: str) -> None:
        if not self._enforce_owner_isolation:
            return

        from deerflow.runtime.user_context import DEFAULT_USER_ID

        skill_dir = self.get_custom_skill_dir(name)
        owner_id = self._read_custom_skill_owner(skill_dir)
        current_user_id = self._current_user_id()
        if owner_id and owner_id == current_user_id:
            return
        if owner_id is None and current_user_id == DEFAULT_USER_ID:
            return
        raise FileNotFoundError(f"Custom skill '{name}' not found.")

    def _ensure_or_claim_custom_skill_owner(self, name: str) -> None:
        if not self._enforce_owner_isolation:
            return

        from deerflow.runtime.user_context import DEFAULT_USER_ID

        skill_dir = self.get_custom_skill_dir(name)
        owner_id = self._read_custom_skill_owner(skill_dir)
        current_user_id = self._current_user_id()
        if owner_id is not None:
            if owner_id and owner_id == current_user_id:
                return
            raise FileNotFoundError(f"Custom skill '{name}' not found.")

        # Legacy custom skills predate ownership metadata. They remain
        # available only to the default migration/CLI account.
        if (skill_dir / SKILL_MD_FILE).exists() and current_user_id != DEFAULT_USER_ID:
            raise FileNotFoundError(f"Custom skill '{name}' not found.")
        self._write_custom_skill_owner(skill_dir)

    def custom_skill_exists(self, name: str) -> bool:
        skill_file = self.get_custom_skill_file(name)
        return skill_file.exists() and self._can_access_custom_skill_dir(skill_file.parent)

    def public_skill_exists(self, name: str) -> bool:
        normalized_name = self.validate_skill_name(name)
        return (self._host_root / SkillCategory.PUBLIC.value / normalized_name / SKILL_MD_FILE).exists()

    def _iter_skill_files(self) -> Iterable[tuple[SkillCategory, Path, Path]]:
        if not self._host_root.exists():
            return
        for category in SkillCategory:
            category_path = self._host_root / category.value
            if not category_path.exists() or not category_path.is_dir():
                continue
            for current_root, dir_names, file_names in os.walk(category_path, followlinks=True):
                dir_names[:] = sorted(name for name in dir_names if not name.startswith("."))
                if SKILL_MD_FILE not in file_names:
                    continue
                skill_dir = Path(current_root)
                if category == SkillCategory.CUSTOM and not self._can_access_custom_skill_dir(skill_dir):
                    continue
                yield category, category_path, skill_dir / SKILL_MD_FILE

    def ensure_custom_skill_is_editable(self, name: str) -> None:
        normalized_name = self.validate_skill_name(name)
        if self.custom_skill_exists(normalized_name):
            return

        skill_dir = self.get_custom_skill_dir(normalized_name)
        if skill_dir.exists():
            if self._can_access_custom_skill_dir(skill_dir):
                return
            raise FileNotFoundError(f"Custom skill '{name}' not found.")

        if self.public_skill_exists(normalized_name):
            if self._enforce_owner_isolation:
                self._write_custom_skill_owner(skill_dir)
            else:
                skill_dir.mkdir(parents=True, exist_ok=True)
            return
        raise FileNotFoundError(f"Custom skill '{name}' not found.")

    def read_custom_skill(self, name: str) -> str:
        if not self.custom_skill_exists(name):
            raise FileNotFoundError(f"Custom skill '{name}' not found.")
        return (self.get_custom_skill_dir(name) / SKILL_MD_FILE).read_text(encoding="utf-8")

    def read_public_skill(self, name: str) -> str:
        if not self.public_skill_exists(name):
            raise FileNotFoundError(f"Public skill '{name}' not found.")
        normalized_name = self.validate_skill_name(name)
        return (self._host_root / SkillCategory.PUBLIC.value / normalized_name / SKILL_MD_FILE).read_text(encoding="utf-8")

    def write_custom_skill(self, name: str, relative_path: str, content: str) -> None:
        self._ensure_or_claim_custom_skill_owner(name)
        target = self.validate_relative_path(relative_path, self.get_custom_skill_dir(name))
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(target.parent),
        ) as tmp_file:
            tmp_file.write(content)
            tmp_path = Path(tmp_file.name)
        tmp_path.replace(target)
        make_skill_path_sandbox_readable(self.get_custom_skill_dir(name))

    async def ainstall_skill_from_archive(self, archive_path: str | Path, *, skip_security_scan: bool = False) -> dict:
        import zipfile

        from deerflow.skills.installer import (
            SkillAlreadyExistsError,
            _move_staged_skill_into_reserved_target,
            _scan_skill_archive_contents_or_raise,
            resolve_skill_dir_from_archive,
            safe_extract_skill_archive,
        )
        from deerflow.skills.validation import _validate_skill_frontmatter

        logger.info("Installing skill from %s", archive_path)
        path = Path(archive_path)
        if not path.is_file():
            if not path.exists():
                raise FileNotFoundError(f"Skill file not found: {archive_path}")
            raise ValueError(f"Path is not a file: {archive_path}")
        if path.suffix not in {".skill", ".zip"}:
            raise ValueError("File must have .skill or .zip extension")

        custom_dir = self._host_root / "custom"
        custom_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            try:
                zf = zipfile.ZipFile(path, "r")
            except FileNotFoundError:
                raise FileNotFoundError(f"Skill file not found: {archive_path}") from None
            except (zipfile.BadZipFile, IsADirectoryError):
                raise ValueError("File is not a valid ZIP archive") from None

            with zf:
                safe_extract_skill_archive(zf, tmp_path)

            skill_dir = resolve_skill_dir_from_archive(tmp_path)

            is_valid, message, skill_name = _validate_skill_frontmatter(skill_dir)
            if not is_valid:
                raise ValueError(f"Invalid skill: {message}")
            if not skill_name or "/" in skill_name or "\\" in skill_name or ".." in skill_name:
                raise ValueError(f"Invalid skill name: {skill_name}")

            target = custom_dir / skill_name
            if target.exists():
                raise SkillAlreadyExistsError(f"Skill '{skill_name}' already exists")

            await _scan_skill_archive_contents_or_raise(skill_dir, skill_name, skip_security_scan=skip_security_scan)

            with tempfile.TemporaryDirectory(prefix=f".installing-{skill_name}-", dir=custom_dir) as staging_root:
                staging_target = Path(staging_root) / skill_name
                shutil.copytree(skill_dir, staging_target)
                _move_staged_skill_into_reserved_target(staging_target, target)
            if self._enforce_owner_isolation:
                self._write_custom_skill_owner(target)
            make_skill_path_sandbox_readable(target)
            logger.info("Skill %r installed to %s", skill_name, target)

        return {
            "success": True,
            "skill_name": skill_name,
            "message": f"Skill '{skill_name}' installed successfully",
        }

    def delete_custom_skill(self, name: str, *, history_meta: dict | None = None) -> None:
        self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(name)
        target = self.get_custom_skill_dir(name)
        if history_meta is not None:
            prev_content = self.read_custom_skill(name)
            try:
                self.append_history(name, {**history_meta, "prev_content": prev_content})
            except OSError as e:
                if not isinstance(e, PermissionError) and e.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}:
                    raise
                logger.warning(
                    "Skipping delete history write for custom skill %s due to readonly/permission failure; continuing with skill directory removal: %s",
                    name,
                    e,
                )
        if target.exists():
            shutil.rmtree(target)

    def append_history(self, name: str, record: dict) -> None:
        self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(name)
        payload = {"ts": datetime.now(UTC).isoformat(), **record}
        history_path = self.get_skill_history_file(name)
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.write("\n")

    def read_history(self, name: str) -> list[dict]:
        self.validate_skill_name(name)
        self._ensure_custom_skill_owner_access(name)
        history_path = self.get_skill_history_file(name)
        if not history_path.exists():
            return []
        records: list[dict] = []
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(json.loads(line))
        return records

    def mkdir_custom_skill_directory(self, name: str, relative_path: str) -> None:
        super().mkdir_custom_skill_directory(name, relative_path)
        make_skill_path_sandbox_readable(self.get_custom_skill_dir(name))

    def write_custom_skill_bytes(self, name: str, relative_path: str, data: bytes) -> None:
        super().write_custom_skill_bytes(name, relative_path, data)
        make_skill_path_sandbox_readable(self.get_custom_skill_dir(name))

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_frontmatter_version_label(skill_md_content: str) -> str | None:
        """Extract optional frontmatter 'version' from SKILL.md content."""
        front_matter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_md_content, re.DOTALL)
        if not front_matter_match:
            return None
        try:
            metadata = yaml.safe_load(front_matter_match.group(1))
        except yaml.YAMLError:
            return None
        if not isinstance(metadata, dict):
            return None
        raw = metadata.get("version")
        if raw is None:
            return None
        label = str(raw).strip()
        return label or None

    def _read_versions_index_records_oldest_first(self, name: str) -> list[dict]:
        index_path = self.get_skill_versions_index_file(name)
        if not index_path.exists():
            return []
        records: list[dict] = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(json.loads(line))
        return records

    def _next_version_seq(self, name: str) -> int:
        records = self._read_versions_index_records_oldest_first(name)
        max_seq = 0
        for record in records:
            seq = record.get("seq")
            if isinstance(seq, int) and seq > max_seq:
                max_seq = seq
        return max_seq + 1

    @staticmethod
    def _walk_dir_stats(root: Path) -> tuple[int, int]:
        """Return (file_count, total_size_bytes) for non-hidden files under root."""
        file_count = 0
        total_size = 0
        for current_root, dir_names, file_names in os.walk(root, followlinks=False):
            dir_names[:] = [d for d in sorted(dir_names) if not d.startswith(".")]
            for filename in file_names:
                if filename.startswith("."):
                    continue
                file_count += 1
                try:
                    total_size += (Path(current_root) / filename).stat().st_size
                except FileNotFoundError:
                    continue
        return file_count, total_size

    def _append_versions_index_record(self, name: str, record: dict) -> None:
        index_path = self.get_skill_versions_index_file(name)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")

    def _write_versions_index_records_oldest_first(self, name: str, records: list[dict]) -> None:
        index_path = self.get_skill_versions_index_file(name)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with index_path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False))
                f.write("\n")

    def _prune_skill_versions(self, name: str) -> None:
        records = self._read_versions_index_records_oldest_first(name)
        if len(records) <= MAX_SKILL_VERSION_SNAPSHOTS:
            return

        newest_records = sorted(
            (record for record in records if isinstance(record.get("seq"), int)),
            key=lambda record: record["seq"],
            reverse=True,
        )
        kept_seqs = {record["seq"] for record in newest_records[:MAX_SKILL_VERSION_SNAPSHOTS]}
        kept_records = [record for record in records if record.get("seq") in kept_seqs]
        pruned_records = [record for record in records if record.get("seq") not in kept_seqs]

        self._write_versions_index_records_oldest_first(name, kept_records)

        versions_root = self.get_skill_versions_dir(name)
        for record in pruned_records:
            seq = record.get("seq")
            if not isinstance(seq, int):
                continue
            version_dir = versions_root / str(seq)
            if version_dir.exists():
                shutil.rmtree(version_dir)

    def _create_skill_version_snapshot(
        self,
        name: str,
        *,
        action: str,
        author: str,
        message: str | None = None,
        thread_id: str | None = None,
        extra_fields: dict | None = None,
        prune_after: bool = True,
    ) -> dict:
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)

        skill_dir = self.get_custom_skill_dir(normalized_name)
        if not skill_dir.exists():
            raise FileNotFoundError(f"Custom skill '{normalized_name}' not found.")

        seq = self._next_version_seq(normalized_name)
        version_dir = self.get_skill_versions_dir(normalized_name) / str(seq)
        if version_dir.exists():
            raise FileExistsError(f"Version snapshot already exists: {version_dir}")

        # Snapshot the full skill directory excluding hidden paths.
        def _ignore_hidden(_dir: str, entries: list[str]) -> list[str]:
            return [e for e in entries if e.startswith(".")]

        shutil.copytree(skill_dir, version_dir, ignore=_ignore_hidden)
        make_skill_path_sandbox_readable(version_dir)

        # Metadata
        skill_md = self.read_custom_skill(normalized_name)
        label = self._extract_frontmatter_version_label(skill_md)
        file_count, size_bytes = self._walk_dir_stats(version_dir)
        created_at = datetime.now(UTC).isoformat()

        record: dict = {
            "seq": seq,
            "created_at": created_at,
            "author": author,
            "action": action,
            "message": message,
            "label": label,
            "thread_id": thread_id,
            "file_count": file_count,
            "size_bytes": size_bytes,
        }
        if extra_fields:
            record.update(extra_fields)
        self._append_versions_index_record(normalized_name, record)
        if prune_after:
            self._prune_skill_versions(normalized_name)
        return record

    def create_skill_version(
        self,
        name: str,
        *,
        action: str,
        author: str,
        message: str | None = None,
        thread_id: str | None = None,
    ) -> dict:
        return self._create_skill_version_snapshot(
            name,
            action=action,
            author=author,
            message=message,
            thread_id=thread_id,
        )

    def list_skill_versions(self, name: str) -> list[dict]:
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        records = self._read_versions_index_records_oldest_first(normalized_name)
        records.reverse()
        return records

    def list_skill_version_files(self, name: str, seq: int) -> list[dict[str, str | int | None]]:
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        normalized_seq = self.validate_skill_version_seq(seq)
        version_dir = self.get_skill_versions_dir(normalized_name) / str(normalized_seq)
        if not version_dir.exists() or not version_dir.is_dir():
            raise FileNotFoundError(f"Version {normalized_seq} not found for skill '{normalized_name}'.")

        entries: list[dict[str, str | int | None]] = []
        for current_root, dir_names, file_names in os.walk(version_dir, followlinks=False):
            dir_names[:] = sorted(d for d in dir_names if not d.startswith("."))
            rel_root = Path(current_root).relative_to(version_dir)
            for directory in sorted(dir_names):
                rel_path = str(rel_root / directory) if rel_root.parts else directory
                entries.append({"path": rel_path.replace("\\", "/"), "type": "directory", "size": None})
            for filename in sorted(file_names):
                if filename.startswith("."):
                    continue
                rel_path = str(rel_root / filename) if rel_root.parts else filename
                file_path = Path(current_root) / filename
                entries.append({"path": rel_path.replace("\\", "/"), "type": "file", "size": file_path.stat().st_size})
        return entries

    def read_skill_version_file(self, name: str, seq: int, relative_path: str) -> str:
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        normalized_seq = self.validate_skill_version_seq(seq)
        version_dir = self.get_skill_versions_dir(normalized_name) / str(normalized_seq)
        if not version_dir.exists() or not version_dir.is_dir():
            raise FileNotFoundError(f"Version {normalized_seq} not found for skill '{normalized_name}'.")

        normalized_path = relative_path.replace("\\", "/").lstrip("/")
        if Path(normalized_path).suffix.lower() in self._BINARY_READ_SUFFIXES:
            raise ValueError(f"Binary file '{normalized_path}' cannot be read as text.")
        target = self.validate_relative_path(normalized_path, version_dir)
        if not target.is_file():
            raise FileNotFoundError(f"File '{normalized_path}' not found for skill '{normalized_name}' version {normalized_seq}.")
        return target.read_text(encoding="utf-8")

    def restore_skill_version(
        self,
        name: str,
        seq: int,
        *,
        author: str,
        thread_id: str | None = None,
    ) -> dict:
        normalized_name = self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(normalized_name)
        normalized_seq = self.validate_skill_version_seq(seq)

        versions_root = self.get_skill_versions_dir(normalized_name)
        source_dir = versions_root / str(normalized_seq)
        if not source_dir.exists() or not source_dir.is_dir():
            raise FileNotFoundError(f"Version {normalized_seq} not found for skill '{normalized_name}'.")

        # Safety: snapshot current state first (if any).
        if self.custom_skill_exists(normalized_name):
            self._create_skill_version_snapshot(
                normalized_name,
                action="restore",
                author=author,
                message=f"pre-restore snapshot (restoring from {normalized_seq})",
                thread_id=thread_id,
                prune_after=False,
            )

        skill_dir = self.get_custom_skill_dir(normalized_name)
        if skill_dir.exists():
            shutil.rmtree(skill_dir)

        # Restore current working directory from snapshot.
        shutil.copytree(source_dir, skill_dir)
        make_skill_path_sandbox_readable(skill_dir)
        if self._enforce_owner_isolation:
            self._write_custom_skill_owner(skill_dir)

        # Record the restored state as a new version snapshot (immutable).
        return self._create_skill_version_snapshot(
            normalized_name,
            action="restore",
            author=author,
            message=f"restored from {normalized_seq}",
            thread_id=thread_id,
            extra_fields={"restored_from": normalized_seq},
        )
