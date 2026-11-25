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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from deerflow.skills.content_rules import scan_content_rules
from deerflow.skills.security_report import STATUS_FAILED, STATUS_PASSED, STATUS_UNSCANNED
from deerflow.skills.security_scanner import SkillScanModelError, create_scan_model, get_scan_concurrency, scan_skill_content

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


class SkillScanModelUnavailableError(Exception):
    """Raised when the model chosen for the security scan cannot be used.

    Deliberately not a ``ValueError`` so route handlers do not mistake it for
    an invalid archive.
    """

    def __init__(self, message: str, *, model_name: str | None = None) -> None:
        super().__init__(message)
        self.model_name = model_name


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


async def _screen_skill_file(
    skill_dir: Path,
    path: Path,
    skill_name: str,
    *,
    executable: bool,
    model=None,
    advisory_enabled: bool = True,
) -> list[dict]:
    """Screen one file: deterministic rules block, LLM advice only warns.

    ``advisory_enabled=False`` (the ``force`` path) skips the LLM call and the
    rule warnings, but deterministic ``block`` findings are always enforced.
    """
    rel_path = path.relative_to(skill_dir).as_posix()
    location = f"{skill_name}/{rel_path}"
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise SkillSecurityScanError(
            f"Security scan failed for skill '{skill_name}': {location} must be valid UTF-8",
            can_force=False,
        ) from e

    findings = scan_content_rules(content, location=location, executable=executable)
    blocking = [finding for finding in findings if finding.severity == "block"]
    if blocking:
        first = blocking[0]
        raise SkillSecurityScanError(
            f"Security scan blocked {location}: rule '{first.rule_id}' at line {first.line}.",
            reason=f"{first.rule_id} at line {first.line}",
            can_force=False,
        )

    if not advisory_enabled:
        return []

    warnings = [finding.as_dict(file=location) for finding in findings]

    if model is None:
        warnings.append(
            {
                "code": "llm_scan_skipped",
                "severity": "warn",
                "file": location,
                "line": None,
                "message": "LLM advisory review was skipped because no scan model was available.",
                "excerpt_hash": None,
                "source": "llm",
            }
        )
        return warnings

    try:
        result = await scan_skill_content(content=content, executable=executable, location=location, model=model)
    except Exception:  # noqa: BLE001 - advisory must never block an upload
        logger.warning("LLM skill review failed for %s; continuing with warnings only", location, exc_info=True)
        warnings.append(
            {
                "code": "llm_scan_skipped",
                "severity": "warn",
                "file": location,
                "line": None,
                "message": "LLM advisory review failed or timed out; review was not blocking.",
                "excerpt_hash": None,
                "source": "llm",
            }
        )
        return warnings

    if getattr(result, "skipped", False):
        warnings.append(
            {
                "code": "llm_scan_skipped",
                "severity": "warn",
                "file": location,
                "line": None,
                "message": "LLM advisory review was unavailable; review was not blocking.",
                "excerpt_hash": None,
                "source": "llm",
            }
        )
    elif result.decision in {"warn", "block"}:
        # The model's reason is intentionally not echoed: it may quote content.
        warnings.append(
            {
                "code": "llm_security_flag",
                "severity": "warn",
                "file": location,
                "line": None,
                "message": f"LLM advisory review flagged this file ({result.decision}).",
                "excerpt_hash": None,
                "source": "llm",
                "decision": result.decision,
            }
        )
    return warnings


async def _screen_paths(
    skill_dir: Path,
    targets: list[tuple[Path, bool]],
    skill_name: str,
    *,
    model,
    max_concurrency: int,
    advisory_enabled: bool = True,
) -> list[dict]:
    """Screen support files concurrently; raise on the first deterministic block."""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _screen_one(path: Path, executable: bool) -> list[dict]:
        async with semaphore:
            return await _screen_skill_file(
                skill_dir,
                path,
                skill_name,
                executable=executable,
                model=model,
                advisory_enabled=advisory_enabled,
            )

    tasks = [asyncio.create_task(_screen_one(path, executable)) for path, executable in targets]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception
        warnings: list[dict] = []
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception() is None:
                warnings.extend(task.result())
        return warnings
    finally:
        leftover = [task for task in tasks if not task.done()]
        for task in leftover:
            task.cancel()
        if leftover:
            await asyncio.gather(*leftover, return_exceptions=True)


@dataclass(slots=True)
class ScreeningOutcome:
    """Result of a full skill screen.

    ``status`` is ``passed`` / ``failed`` / ``unscanned``.  ``failed`` means the
    advisory LLM review explicitly flagged the content as unsafe; the skill is
    still installed (the uploader sees a warning) but is hidden from the model.
    ``unscanned`` means the LLM review did not run, so the background rescan
    will pick the skill up later.
    """

    warnings: list[dict]
    status: str
    reason: str | None


async def _screen_skill_archive_contents(
    skill_dir: Path,
    skill_name: str,
    *,
    skip_warnings: bool = False,
    model_name: str | None = None,
) -> ScreeningOutcome:
    """Screen all installable text and script files and return advisory warnings.

    Deterministic rule violations raise :class:`SkillSecurityScanError` with
    ``can_force=False``.  Everything else (rule warnings, LLM flags, LLM
    unavailability) is returned for the uploader to see and for auditing.
    """
    skill_md = skill_dir / "SKILL.md"
    scan_model = None
    if not skip_warnings:
        try:
            scan_model = create_scan_model(model_name=model_name)
        except SkillScanModelError as e:
            # Advisory only: a missing/broken model must not fail the upload.
            logger.warning("Skill security review model unavailable: %s", e)
            scan_model = None

    # SKILL.md is screened first for ordering, but all targets run concurrently
    # so a deterministic block is detected immediately instead of waiting for a
    # slow LLM review of another file.
    scan_targets: list[tuple[Path, bool]] = [(skill_md, False)]
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue

        rel_path = path.relative_to(skill_dir)
        if rel_path == Path("SKILL.md"):
            continue
        if path.name == "SKILL.md":
            raise SkillSecurityScanError(
                f"Security scan failed for skill '{skill_name}': nested SKILL.md is not allowed at {skill_name}/{rel_path.as_posix()}",
                can_force=False,
            )
        if not _should_scan_support_file(rel_path):
            continue

        scan_targets.append((path, _is_script_support_file(rel_path)))

    warnings = await _screen_paths(
        skill_dir,
        scan_targets,
        skill_name,
        model=scan_model,
        max_concurrency=get_scan_concurrency(),
        advisory_enabled=not skip_warnings,
    )

    if skip_warnings:
        return ScreeningOutcome(
            warnings=[],
            status=STATUS_UNSCANNED,
            reason="security warnings were skipped by an explicit force request; pending background rescan",
        )

    skipped_warnings = [warning for warning in warnings if warning.get("code") == "llm_scan_skipped"]
    blocking_flags = [warning for warning in warnings if warning.get("decision") == "block"]
    if skipped_warnings:
        return ScreeningOutcome(
            warnings=warnings,
            status=STATUS_UNSCANNED,
            reason="LLM advisory review was unavailable; pending background rescan",
        )
    if blocking_flags:
        return ScreeningOutcome(
            warnings=warnings,
            status=STATUS_FAILED,
            reason=f"LLM advisory review flagged {blocking_flags[0].get('file') or 'skill content'} as unsafe",
        )
    return ScreeningOutcome(warnings=warnings, status=STATUS_PASSED, reason=None)


def _run_async_install(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
