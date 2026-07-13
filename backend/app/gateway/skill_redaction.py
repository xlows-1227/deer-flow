"""Gateway helpers for user-visible Skill content redaction."""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from deerflow.config.app_config import get_app_config
from deerflow.runtime import serialize, serialize_channel_values
from deerflow.skills.privacy import SkillContentRedactor, record_skill_redaction_metric

_LEGACY_CONTEXT_TOOL_NAMES = frozenset({"read_file", "bash", "grep", "glob", "ls"})
_LEGACY_CONTEXT_PAGE_SIZE = 200
_LEGACY_CONTEXT_MAX_PAGES = 1000


def make_gateway_skill_redactor(app_config: Any | None = None) -> SkillContentRedactor:
    try:
        config = app_config or get_app_config()
    except Exception:
        # A configuration failure must never turn into a raw-payload fallback.
        # Treat every absolute tool path as potentially protected until the
        # configured Skill root can be resolved again.
        record_skill_redaction_metric(
            "skill_redaction_errors_total",
            boundary="gateway",
        )
        return SkillContentRedactor(
            skills_root="/mnt/skills",
            redact_unknown_paths=True,
            boundary="gateway",
        )
    skills = getattr(config, "skills", None)
    root = getattr(skills, "container_path", None)
    return SkillContentRedactor(
        skills_root=root if isinstance(root, str) and root else "/mnt/skills",
        boundary="gateway",
    )


def redact_channel_values(
    channel_values: Mapping[str, Any],
    *,
    boundary_id: str,
    app_config: Any | None = None,
) -> dict[str, Any]:
    redactor = make_gateway_skill_redactor(app_config)
    safe_values = redactor.redact_stream_payload("values", channel_values, run_id=boundary_id)
    return serialize_channel_values(safe_values) if isinstance(safe_values, dict) else {}


def redact_user_payload(
    payload: Any,
    *,
    boundary_id: str,
    mode: str = "payload",
    app_config: Any | None = None,
) -> Any:
    redactor = make_gateway_skill_redactor(app_config)
    safe_payload = redactor.redact_stream_payload(mode, payload, run_id=boundary_id)
    return serialize(safe_payload, mode=mode if mode in {"values", "messages"} else "")


def _content(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = row.get("content")
    return value if isinstance(value, Mapping) else None


def _observed_tool_call_ids(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    observed: set[str] = set()
    for row in rows:
        content = _content(row)
        if content is None:
            continue
        tool_calls = content.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if isinstance(tool_call, Mapping) and isinstance(tool_call.get("id"), str):
                observed.add(tool_call["id"])
    return observed


def _legacy_results_needing_context(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    observed = _observed_tool_call_ids(rows)
    missing: set[str] = set()
    for row in rows:
        metadata = row.get("metadata")
        if isinstance(metadata, Mapping) and isinstance(metadata.get("skill_execution"), Mapping):
            continue
        content = _content(row)
        if content is None or content.get("type") != "tool":
            continue
        tool_call_id = content.get("tool_call_id")
        name = content.get("name")
        if isinstance(tool_call_id, str) and tool_call_id not in observed and name in _LEGACY_CONTEXT_TOOL_NAMES:
            missing.add(tool_call_id)
    return missing


def _inject_fail_closed_metadata(rows: list[dict[str, Any]], unresolved: set[str]) -> list[dict[str, Any]]:
    if not unresolved:
        return rows
    safe_rows = copy.deepcopy(rows)
    for row in safe_rows:
        content = row.get("content")
        if not isinstance(content, dict) or content.get("tool_call_id") not in unresolved:
            continue
        additional_kwargs = content.get("additional_kwargs")
        safe_additional = dict(additional_kwargs) if isinstance(additional_kwargs, Mapping) else {}
        safe_additional["visibility"] = "redacted"
        safe_additional["event_type"] = "skill_execution"
        safe_additional["skill_execution"] = {"summary": "Loaded skill instructions"}
        content["additional_kwargs"] = safe_additional
    return safe_rows


async def redact_run_event_rows(
    event_store: Any,
    rows: Iterable[Mapping[str, Any]],
    *,
    thread_id: str,
    run_id: str,
    app_config: Any | None = None,
) -> list[dict[str, Any]]:
    """Redact one run's event page, resolving legacy cross-page tool calls."""

    materialized = [copy.deepcopy(dict(row)) for row in rows]
    redactor = make_gateway_skill_redactor(app_config)
    unresolved = _legacy_results_needing_context(materialized)
    record_skill_redaction_metric(
        "skill_redaction_legacy_fallback_total",
        boundary="gateway",
        count=len(unresolved),
    )
    cursor_candidates = [row.get("seq") for row in materialized if isinstance(row.get("seq"), int)]
    before_seq = min(cursor_candidates) if cursor_candidates else None

    for _ in range(_LEGACY_CONTEXT_MAX_PAGES):
        if not unresolved or before_seq is None:
            break
        older = await event_store.list_messages_by_run(
            thread_id,
            run_id,
            limit=_LEGACY_CONTEXT_PAGE_SIZE,
            before_seq=before_seq,
            after_seq=None,
        )
        if not older:
            break
        redactor.redact_event_batch(older)
        unresolved.difference_update(_observed_tool_call_ids(older))
        older_seqs = [row.get("seq") for row in older if isinstance(row.get("seq"), int)]
        next_before = min(older_seqs) if older_seqs else None
        if next_before is None or next_before >= before_seq:
            break
        before_seq = next_before

    materialized = _inject_fail_closed_metadata(materialized, unresolved)
    record_skill_redaction_metric(
        "skill_redaction_fail_closed_total",
        boundary="gateway",
        count=len(unresolved),
    )
    return redactor.redact_event_batch(materialized)


async def redact_thread_event_rows(
    event_store: Any,
    rows: Iterable[Mapping[str, Any]],
    *,
    thread_id: str,
    app_config: Any | None = None,
) -> list[dict[str, Any]]:
    """Redact a thread page containing events from multiple runs."""

    materialized = [copy.deepcopy(dict(row)) for row in rows]
    by_run: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(materialized):
        by_run[str(row.get("run_id") or "")].append((index, row))

    redacted_by_index: dict[int, dict[str, Any]] = {}
    for run_id, indexed_rows in by_run.items():
        safe_rows = await redact_run_event_rows(
            event_store,
            [row for _, row in indexed_rows],
            thread_id=thread_id,
            run_id=run_id,
            app_config=app_config,
        )
        for (index, _), safe_row in zip(indexed_rows, safe_rows, strict=True):
            redacted_by_index[index] = safe_row
    return [redacted_by_index[index] for index in range(len(materialized))]
