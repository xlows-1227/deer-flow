"""Shared skill archive installation logic.

Pure business logic — no FastAPI/HTTP dependencies.
Both Gateway and Client delegate to these functions.
"""

import asyncio
import concurrent.futures
import logging
import os
import posixpath
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from deerflow.skills.security_scanner import scan_skill_content

logger = logging.getLogger(__name__)

_PROMPT_INPUT_DIRS = {"references", "templates"}
_PROMPT_INPUT_SUFFIXES = frozenset({".json", ".markdown", ".md", ".rst", ".txt", ".yaml", ".yml"})
_SANDBOX_READABLE_DIR_MODE = 0o755
_SANDBOX_READABLE_FILE_MODE = 0o644
_SANDBOX_READABLE_EXECUTABLE_MODE = 0o755


class SkillAlreadyExistsError(ValueError):
    """Raised when a skill with the same name is already installed."""


class SkillSecurityScanError(ValueError):
    """Raised when a skill archive fails security scanning."""

    def __init__(self, message: str, *, reason: str | None = None, can_force: bool = False) -> None:
        super().__init__(message)
        self.reason = reason or message
        self.can_force = can_force


def is_unsafe_zip_member(info: zipfile.ZipInfo) -> bool:
    """Return True if the zip member path is absolute or attempts directory traversal."""
    name = info.filename
    if not name:
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    path = PurePosixPath(normalized)
    if path.is_absolute():
        return True
    if PureWindowsPath(name).is_absolute():
        return True
    if ".." in path.parts:
        return True
    return False


def is_symlink_member(info: zipfile.ZipInfo) -> bool:
    """Detect symlinks based on the external attributes stored in the ZipInfo."""
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def should_ignore_archive_entry(path: Path) -> bool:
    """Return True for macOS metadata dirs and dotfiles."""
    return path.name.startswith(".") or path.name == "__MACOSX"


def _file_mode_for_sandbox(path: Path) -> int:
    current_mode = stat.S_IMODE(path.stat().st_mode)
    if current_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        return _SANDBOX_READABLE_EXECUTABLE_MODE
    return _SANDBOX_READABLE_FILE_MODE


def make_skill_path_sandbox_readable(path: Path) -> None:
    """Ensure skill files are readable after being bind-mounted into a sandbox.

    Custom skills are authored by the Gateway process, but read by a separate
    sandbox container that may run as a different uid.  Normalise permissions
    to avoid 0600 files or 0700 directories becoming unreadable in Docker/K8s.
    Symlinks are intentionally skipped so chmod never escapes the skill tree.
    """
    if not path.exists() or path.is_symlink():
        return

    if path.is_file():
        path.chmod(_file_mode_for_sandbox(path))
        return

    if not path.is_dir():
        return

    for current_root, dir_names, file_names in os.walk(path, followlinks=False):
        root = Path(current_root)
        if root.is_symlink():
            dir_names[:] = []
            continue
        root.chmod(_SANDBOX_READABLE_DIR_MODE)

        dir_names[:] = [name for name in dir_names if not name.startswith(".") and not (root / name).is_symlink()]
        for filename in file_names:
            if filename.startswith("."):
                continue
            file_path = root / filename
            if file_path.is_symlink() or not file_path.is_file():
                continue
            file_path.chmod(_file_mode_for_sandbox(file_path))


def resolve_skill_dir_from_archive(temp_path: Path) -> Path:
    """Locate the skill root directory from extracted archive contents.

    Filters out macOS metadata (__MACOSX) and dotfiles (.DS_Store).

    Returns:
        Path to the skill directory.

    Raises:
        ValueError: If the archive is empty after filtering.
    """
    items = [p for p in temp_path.iterdir() if not should_ignore_archive_entry(p)]
    if not items:
        raise ValueError("Skill archive is empty")
    if len(items) == 1 and items[0].is_dir():
        return items[0]
    return temp_path


def safe_extract_skill_archive(
    zip_ref: zipfile.ZipFile,
    dest_path: Path,
    max_total_size: int = 512 * 1024 * 1024,
) -> None:
    """Safely extract a skill archive with security protections.

    Protections:
    - Reject absolute paths and directory traversal (..).
    - Skip symlink entries instead of materialising them.
    - Enforce a hard limit on total uncompressed size (zip bomb defence).

    Raises:
        ValueError: If unsafe members or size limit exceeded.
    """
    dest_root = dest_path.resolve()
    total_written = 0

    for info in zip_ref.infolist():
        if is_unsafe_zip_member(info):
            raise ValueError(f"Archive contains unsafe member path: {info.filename!r}")

        if is_symlink_member(info):
            logger.warning("Skipping symlink entry in skill archive: %s", info.filename)
            continue

        normalized_name = posixpath.normpath(info.filename.replace("\\", "/"))
        member_path = dest_root.joinpath(*PurePosixPath(normalized_name).parts)
        if not member_path.resolve().is_relative_to(dest_root):
            raise ValueError(f"Zip entry escapes destination: {info.filename!r}")
        member_path.parent.mkdir(parents=True, exist_ok=True)

        if info.is_dir():
            member_path.mkdir(parents=True, exist_ok=True)
            continue

        with zip_ref.open(info) as src, member_path.open("wb") as dst:
            while chunk := src.read(65536):
                total_written += len(chunk)
                if total_written > max_total_size:
                    raise ValueError("Skill archive is too large or appears highly compressed.")
                dst.write(chunk)


def _is_script_support_file(rel_path: Path) -> bool:
    return bool(rel_path.parts) and rel_path.parts[0] == "scripts"


def _should_scan_support_file(rel_path: Path) -> bool:
    if _is_script_support_file(rel_path):
        return True
    return bool(rel_path.parts) and rel_path.parts[0] in _PROMPT_INPUT_DIRS and rel_path.suffix.lower() in _PROMPT_INPUT_SUFFIXES


def _move_staged_skill_into_reserved_target(staging_target: Path, target: Path) -> None:
    installed = False
    reserved = False
    try:
        target.mkdir(mode=_SANDBOX_READABLE_DIR_MODE)
        reserved = True
        for child in staging_target.iterdir():
            shutil.move(str(child), target / child.name)
        make_skill_path_sandbox_readable(target)
        installed = True
    except FileExistsError as e:
        raise SkillAlreadyExistsError(f"Skill '{target.name}' already exists") from e
    finally:
        if reserved and not installed and target.exists():
            shutil.rmtree(target)


async def _scan_skill_file_or_raise(skill_dir: Path, path: Path, skill_name: str, *, executable: bool) -> None:
    rel_path = path.relative_to(skill_dir).as_posix()
    location = f"{skill_name}/{rel_path}"
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise SkillSecurityScanError(f"Security scan failed for skill '{skill_name}': {location} must be valid UTF-8", can_force=True) from e

    try:
        result = await scan_skill_content(content, executable=executable, location=location)
    except Exception as e:
        raise SkillSecurityScanError(f"Security scan failed for {location}: {e}", reason=str(e), can_force=True) from e

    decision = getattr(result, "decision", None)
    reason = str(getattr(result, "reason", "") or "No reason provided.")
    if decision == "block":
        if rel_path == "SKILL.md":
            raise SkillSecurityScanError(f"Security scan blocked skill '{skill_name}': {reason}", reason=reason, can_force=True)
        raise SkillSecurityScanError(f"Security scan blocked {location}: {reason}", reason=reason, can_force=True)
    if executable and decision != "allow":
        raise SkillSecurityScanError(f"Security scan rejected executable {location}: {reason}", reason=reason, can_force=True)
    if decision not in {"allow", "warn"}:
        raise SkillSecurityScanError(f"Security scan failed for {location}: invalid scanner decision {decision!r}", reason=reason, can_force=True)


async def _scan_skill_archive_contents_or_raise(skill_dir: Path, skill_name: str, *, skip_security_scan: bool = False) -> None:
    """Run the skill security scanner against all installable text and script files."""
    skill_md = skill_dir / "SKILL.md"
    if not skip_security_scan:
        await _scan_skill_file_or_raise(skill_dir, skill_md, skill_name, executable=False)

    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue

        rel_path = path.relative_to(skill_dir)
        if rel_path == Path("SKILL.md"):
            continue
        if path.name == "SKILL.md":
            raise SkillSecurityScanError(f"Security scan failed for skill '{skill_name}': nested SKILL.md is not allowed at {skill_name}/{rel_path.as_posix()}")
        if skip_security_scan:
            continue
        if not _should_scan_support_file(rel_path):
            continue

        await _scan_skill_file_or_raise(skill_dir, path, skill_name, executable=_is_script_support_file(rel_path))


def _run_async_install(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
