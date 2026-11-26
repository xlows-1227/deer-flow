"""Background rescan for skills whose security status is ``unscanned``.

The loop lives in ``app.gateway.skill_security_scheduler``; this module holds
the per-skill logic so it can be unit tested without a running app.

Design notes:

* Only ``unscanned`` skills are selected.  ``passed`` / ``failed`` are stable
  until the content changes (which the editor endpoints reset to
  ``unscanned``).
* One process-wide in-flight set plus a per-directory lock prevents the same
  skill from being scanned twice concurrently (scheduler tick overlap, or a
  manual trigger racing the loop).
* ``unscanned`` is retried on the next tick: if the model is still
  unavailable, the status is rewritten with a fresh reason and the skill stays
  in the queue instead of silently becoming ``passed``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TypedDict
from weakref import WeakValueDictionary

from deerflow.skills.installer import SkillSecurityScanError, _screen_skill_archive_contents
from deerflow.skills.security_report import (
    REASON_CODE_RULE_BLOCK,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_UNSCANNED,
    compute_content_hash,
    read_security_status,
    record_scan_result,
)

logger = logging.getLogger(__name__)


class RescanResult(TypedDict):
    """Shape returned by :func:`rescan_skill_dir`.

    ``status`` is ``None`` only for the "already in flight" short-circuit;
    otherwise it is one of the persisted security statuses.
    """

    scanned: bool
    status: str | None
    reason: str | None
    changed: bool


_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_inflight: set[str] = set()


def _key(skill_dir: Path) -> str:
    try:
        return str(skill_dir.resolve())
    except OSError:
        return str(skill_dir)


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def rescan_skill_dir(skill_dir: Path, *, model_name: str | None = None) -> RescanResult:
    """Rescan one skill if it is still ``unscanned``.

    Returns ``{"scanned", "status", "reason", "changed"}``; ``status`` is
    ``None`` when another scan for the same directory is already in flight.
    """
    key = _key(skill_dir)
    if key in _inflight:
        return {"scanned": False, "status": None, "reason": "already in flight", "changed": False}

    _inflight.add(key)
    try:
        async with _lock_for(key):
            current = read_security_status(skill_dir)
            if current["status"] != STATUS_UNSCANNED:
                return {"scanned": False, "status": current["status"], "reason": None, "changed": False}

            hash_before = compute_content_hash(skill_dir)
            try:
                outcome = await _screen_skill_archive_contents(
                    skill_dir,
                    skill_dir.name,
                    model_name=model_name,
                )
                status, reason, warnings = outcome.status, outcome.reason, outcome.warnings
                reason_code = outcome.reason_code
            except SkillSecurityScanError as e:
                status = STATUS_FAILED
                reason = str(e.reason)
                warnings = []
                reason_code = e.reason_code or REASON_CODE_RULE_BLOCK

            hash_after = compute_content_hash(skill_dir)
            if hash_after != hash_before:
                # The skill was edited while the scan was running.  Discard this
                # result so a stale verdict is never pinned to newer content.
                status = STATUS_UNSCANNED
                reason = "content changed during rescan; will retry"
                warnings = []
                reason_code = None

            record_scan_result(
                skill_dir,
                status=status,
                reason=reason,
                warnings=warnings,
                content_hash=hash_after,
                reason_code=reason_code,
            )
            logger.info(
                "Skill security rescan: skill=%s status=%s reason_code=%s changed=%s reason=%s",
                skill_dir.name,
                status,
                reason_code,
                status != current["status"],
                reason,
            )
            # ``unscanned`` means the model was still unavailable; it stays in
            # the queue and is retried on the next tick.
            return {
                "scanned": status != STATUS_UNSCANNED,
                "status": status,
                "reason": reason,
                "changed": status != current["status"],
            }
    finally:
        _inflight.discard(key)


def list_unscanned_skill_dirs(skills_root: Path) -> list[Path]:
    custom_dir = skills_root / "custom"
    if not custom_dir.is_dir():
        return []
    candidates: list[Path] = []
    for child in sorted(custom_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / "SKILL.md").is_file():
            continue
        if read_security_status(child)["status"] == STATUS_UNSCANNED:
            candidates.append(child)
    return candidates


async def _refresh_prompt_cache_after_status_change() -> None:
    """Rebuild the cached skills prompt section after a background verdict flip.

    The prompt cache key only covers skill content, so a status change
    (``unscanned`` -> ``passed``/``failed``) would otherwise stay invisible to
    the model until an unrelated skill edit clears the cache.
    """
    from deerflow.agents.lead_agent.prompt import refresh_skills_system_prompt_cache_async

    try:
        await refresh_skills_system_prompt_cache_async()
    except Exception:
        logger.exception("Failed to refresh the skills system prompt cache after a rescan")


def _empty_summary(*, candidates: int = 0) -> dict[str, int]:
    return {"candidates": candidates, "processed": 0, "passed": 0, "failed": 0, "unscanned": 0, "changed": 0}


async def rescan_unscanned_skills(
    skills_root: Path,
    *,
    batch_size: int = 5,
    concurrency: int = 1,
    model_name: str | None = None,
) -> dict:
    """Rescan at most ``batch_size`` unscanned skills per call.

    Batch + concurrency bounds keep a large backlog of legacy skills from
    saturating the model provider in one tick.
    """
    candidates = list_unscanned_skill_dirs(skills_root)
    batch = candidates[: max(1, batch_size)]
    if not batch:
        return _empty_summary()

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(skill_dir: Path) -> RescanResult:
        async with semaphore:
            return await rescan_skill_dir(skill_dir, model_name=model_name)

    results = await asyncio.gather(*(_one(skill_dir) for skill_dir in batch), return_exceptions=True)
    summary = _empty_summary(candidates=len(candidates))
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("Background skill rescan failed: %s", result)
            summary["unscanned"] += 1
            continue
        summary["processed"] += 1
        status = result.get("status")
        if status == STATUS_PASSED:
            summary["passed"] += 1
        elif status == STATUS_FAILED:
            summary["failed"] += 1
        else:
            summary["unscanned"] += 1
        if result.get("changed"):
            summary["changed"] += 1

    if summary["changed"]:
        await _refresh_prompt_cache_after_status_change()

    return summary
