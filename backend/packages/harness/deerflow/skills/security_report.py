"""Persist and read the security report of an installed skill.

The report lives next to the skill as ``.security-scan.json`` so the skill
list/detail pages can show a constant reminder without rescanning, and so
dismissals can be tied to the exact content hash they were made for.

Dismissal semantics: a dismissal only applies while the skill content hash is
unchanged.  Editing the skill changes the hash, which makes every previous
dismissal inert and the warnings visible again.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SECURITY_REPORT_FILENAME = ".security-scan.json"
SECURITY_OVERRIDE_FILENAME = ".security-override.json"
REPORT_VERSION = 2

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_UNSCANNED = "unscanned"
# Emitted only by :func:`read_security_status` when a human explicitly accepted
# a ``failed`` verdict for the exact content that was scanned.  It is never
# persisted as a report status.
STATUS_OVERRIDDEN = "overridden"
SECURITY_STATUSES = (STATUS_PASSED, STATUS_FAILED, STATUS_UNSCANNED)

# Why a skill ended up ``failed``.  Informational only: every failed skill can
# be overridden by its owner or an admin.
REASON_CODE_LLM_BLOCK = "llm_block"
REASON_CODE_RULE_BLOCK = "rule_block"
REASON_CODE_STRUCTURAL = "structural"
SECURITY_REASON_CODES = (REASON_CODE_LLM_BLOCK, REASON_CODE_RULE_BLOCK, REASON_CODE_STRUCTURAL)


def _normalize_status(status: str | None) -> str:
    """Coerce a requested/persisted status into one of ``SECURITY_STATUSES``."""
    return status if status in SECURITY_STATUSES else STATUS_UNSCANNED


def _normalize_reason_code(reason_code: str | None) -> str | None:
    """Keep only known failure classes; unknown values carry no meaning."""
    return reason_code if reason_code in SECURITY_REASON_CODES else None


def _is_ignored(rel_path: str) -> bool:
    return any(part.startswith(".") for part in rel_path.split("/"))


def compute_content_hash(skill_dir: Path) -> str:
    """Hash every user-visible file so dismissals can be pinned to content."""
    digest = hashlib.sha256()
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(skill_dir).as_posix()
        if _is_ignored(rel_path):
            continue
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def write_security_report(
    skill_dir: Path,
    *,
    content_hash: str,
    warnings: list[dict],
    status: str = STATUS_PASSED,
    reason: str | None = None,
    reason_code: str | None = None,
) -> dict:
    now = datetime.now(UTC).isoformat()
    report = {
        "version": REPORT_VERSION,
        "status": _normalize_status(status),
        "reason": reason,
        "reason_code": _normalize_reason_code(reason_code),
        "content_hash": content_hash,
        "scanned_at": now,
        "checked_at": now,
        "warnings": warnings,
        "dismissals": [],
    }
    _write_report_file(skill_dir, report)
    return report


def _write_report_file(skill_dir: Path, report: dict) -> None:
    try:
        (skill_dir / SECURITY_REPORT_FILENAME).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("Unable to persist security report for %s", skill_dir, exc_info=True)


def update_security_status(
    skill_dir: Path,
    *,
    status: str,
    reason: str | None,
    content_hash: str | None = None,
    warnings: list[dict] | None = None,
    keep_warnings: bool = True,
    reason_code: str | None = None,
) -> dict:
    """Update the status fields while preserving unrelated report data."""
    report = read_security_report(skill_dir) or {
        "version": REPORT_VERSION,
        "content_hash": content_hash or compute_content_hash(skill_dir),
        "scanned_at": None,
        "warnings": [],
        "dismissals": [],
    }
    report["version"] = REPORT_VERSION
    report["status"] = _normalize_status(status)
    report["reason"] = reason
    report["reason_code"] = _normalize_reason_code(reason_code)
    report["checked_at"] = datetime.now(UTC).isoformat()
    if content_hash is not None:
        report["content_hash"] = content_hash
    if warnings is not None:
        report["warnings"] = warnings
    elif not keep_warnings:
        report["warnings"] = []
    _write_report_file(skill_dir, report)
    return report


def record_scan_result(
    skill_dir: Path,
    *,
    status: str,
    reason: str | None,
    warnings: list[dict],
    content_hash: str | None = None,
    reason_code: str | None = None,
) -> dict:
    """Merge a (re)scan result into the report, preserving existing dismissals."""
    report = read_security_report(skill_dir) or {
        "version": REPORT_VERSION,
        "dismissals": [],
    }
    now = datetime.now(UTC).isoformat()
    report.update(
        {
            "version": REPORT_VERSION,
            "status": _normalize_status(status),
            "reason": reason,
            "reason_code": _normalize_reason_code(reason_code),
            "content_hash": content_hash or compute_content_hash(skill_dir),
            "scanned_at": now,
            "checked_at": now,
            "warnings": warnings,
        }
    )
    report.setdefault("dismissals", [])
    _write_report_file(skill_dir, report)
    return report


def mark_skill_unscanned(skill_dir: Path, *, reason: str = "content changed; pending background rescan") -> dict:
    """Reset a skill to ``unscanned`` after an edit (no model call here)."""
    return update_security_status(
        skill_dir,
        status=STATUS_UNSCANNED,
        reason=reason,
        content_hash=compute_content_hash(skill_dir),
        warnings=[],
    )


def effective_reason_code(report: dict | None) -> str | None:
    """Derive the failure class, including reports written before it existed.

    Legacy ``failed`` reports only carry warnings; a blocking LLM warning is
    enough evidence that the failure was an advisory ``block``.
    """
    report = report or {}
    code = report.get("reason_code")
    if code in SECURITY_REASON_CODES:
        return code
    if report.get("status") != STATUS_FAILED:
        return None
    for warning in report.get("warnings") or []:
        if isinstance(warning, dict) and warning.get("decision") == "block" and warning.get("source", "llm") == "llm":
            return REASON_CODE_LLM_BLOCK
    return None


def read_security_status(skill_dir: Path) -> dict:
    """Return the persisted status, merged with a human override if active.

    A missing or unreadable report means ``passed``: skills that predate the
    status field were scanned by the old upload flow, so they are neither shown
    as a warning nor queued for a background rescan.  Only this change may
    introduce ``unscanned`` (an LLM skip at install/edit time).

    ``overridden`` is derived, never stored: it requires a ``failed`` report
    plus an override record pinned to that report's content hash.  Any content
    change updates the report hash, which makes the override inert again.
    """
    report = read_security_report(skill_dir) or {}
    status = report.get("status")
    if status not in SECURITY_STATUSES:
        status = STATUS_PASSED
    override = active_override(skill_dir, report=report)
    if override is not None:
        status = STATUS_OVERRIDDEN
    return {
        "status": status,
        "reason": report.get("reason"),
        "reason_code": effective_reason_code(report),
        "checked_at": report.get("checked_at") or report.get("scanned_at"),
        "override": override,
    }


def read_override(skill_dir: Path) -> dict | None:
    path = skill_dir / SECURITY_OVERRIDE_FILENAME
    if not path.is_file():
        return None
    try:
        override = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable security override at %s", path, exc_info=True)
        return None
    return override if isinstance(override, dict) else None


def active_override(skill_dir: Path, *, report: dict | None = None) -> dict | None:
    """Return the override only while it matches the currently failed content."""
    report = report if report is not None else (read_security_report(skill_dir) or {})
    if report.get("status") != STATUS_FAILED:
        return None
    override = read_override(skill_dir)
    if not override:
        return None
    if not override.get("content_hash") or override.get("content_hash") != report.get("content_hash"):
        return None
    return override


def write_override(
    skill_dir: Path,
    *,
    content_hash: str,
    actor: dict,
    reason: str | None,
    acknowledged: list[dict],
) -> dict:
    """Persist a human acceptance of a ``failed`` verdict for this content hash."""
    override = {
        "version": 1,
        "content_hash": content_hash,
        "actor": actor,
        "at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "acknowledged": acknowledged,
    }
    (skill_dir / SECURITY_OVERRIDE_FILENAME).write_text(
        json.dumps(override, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return override


def delete_override(skill_dir: Path) -> bool:
    path = skill_dir / SECURITY_OVERRIDE_FILENAME
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def read_security_report(skill_dir: Path) -> dict | None:
    path = skill_dir / SECURITY_REPORT_FILENAME
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable security report at %s", path, exc_info=True)
        return None
    return report if isinstance(report, dict) else None


def effective_warnings(skill_dir: Path) -> dict:
    """Return the warnings still in force for the current content."""
    report = read_security_report(skill_dir)
    if not report:
        return {
            "content_hash": None,
            "scanned_at": None,
            "stale": False,
            "warnings": [],
            "status": STATUS_PASSED,
            "reason": None,
            "reason_code": None,
            "override": None,
        }

    current_hash = compute_content_hash(skill_dir)
    stored_hash = report.get("content_hash")
    dismissed: set[str] = set()
    for entry in report.get("dismissals") or []:
        if isinstance(entry, dict) and entry.get("content_hash") == current_hash:
            dismissed.update(entry.get("codes") or [])

    warnings = [warning for warning in (report.get("warnings") or []) if isinstance(warning, dict) and warning.get("code") not in dismissed]
    status_info = read_security_status(skill_dir)
    return {
        "content_hash": current_hash,
        "scanned_at": report.get("scanned_at"),
        "stale": stored_hash != current_hash,
        "warnings": warnings,
        "status": status_info["status"],
        "reason": status_info["reason"],
        "reason_code": status_info["reason_code"],
        "override": status_info["override"],
    }


def dismiss_warnings(
    skill_dir: Path,
    codes: list[str],
    *,
    actor: str,
) -> dict:
    """Record a per-install dismissal and return the remaining warnings."""
    report = read_security_report(skill_dir) or {
        "version": REPORT_VERSION,
        "content_hash": compute_content_hash(skill_dir),
        "scanned_at": datetime.now(UTC).isoformat(),
        "warnings": [],
        "dismissals": [],
    }
    current_hash = compute_content_hash(skill_dir)
    dismissals = [entry for entry in (report.get("dismissals") or []) if isinstance(entry, dict)]
    dismissals.append(
        {
            "content_hash": current_hash,
            "codes": sorted({str(code) for code in codes}),
            "by": actor,
            "at": datetime.now(UTC).isoformat(),
        }
    )
    report["dismissals"] = dismissals
    try:
        (skill_dir / SECURITY_REPORT_FILENAME).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("Unable to persist security report for %s", skill_dir, exc_info=True)
    return effective_warnings(skill_dir)


def warning_count(skill_dir: Path) -> int:
    """Count active warnings without hashing the whole skill.

    List endpoints call this for every skill, so it relies on the stored report
    instead of ``compute_content_hash`` (which would read every file).
    """
    report = read_security_report(skill_dir) or {}
    stored_hash = report.get("content_hash")
    dismissed: set[str] = set()
    for entry in report.get("dismissals") or []:
        if isinstance(entry, dict) and entry.get("content_hash") == stored_hash:
            dismissed.update(entry.get("codes") or [])
    return sum(1 for warning in (report.get("warnings") or []) if isinstance(warning, dict) and warning.get("code") not in dismissed)
