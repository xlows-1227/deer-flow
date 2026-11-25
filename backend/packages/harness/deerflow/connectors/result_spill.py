"""Spill oversized connector tool results to a per-thread file.

Small results keep the exact legacy inline shape (``model_dump()`` untouched).
Only results whose serialized size exceeds ``connectors.result_inline_max_chars``
are written to ``/mnt/user-data/workspace/connector_results/`` so the model can
read them back with ``read_file`` instead of loading everything into context.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.config import get_app_config
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.connectors.schemas import ConnectorRuntimeContext

logger = logging.getLogger(__name__)

SPILL_SUBDIR = "connector_results"
_SQL_PREVIEW_CHARS = 500
_PREVIEW_TEXT_CHARS = 1000
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_INCOMPLETE_WARNING = "Result was truncated at the database max_rows limit; the data is incomplete and must not be used for statistics or totals."
_INCOMPLETE_NEXT_STEPS = [
    "Add WHERE filters to narrow the result set.",
    "Run an aggregate query (COUNT / SUM / GROUP BY) instead of selecting raw rows.",
    "Page through the rows with LIMIT / OFFSET or an incremental key.",
]


def serialized_size(value: Any) -> int:
    """Return the character length of the compact JSON representation."""
    return len(json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")))


def _inline_max_chars() -> int:
    try:
        return int(get_app_config().connectors.result_inline_max_chars)
    except Exception:  # noqa: BLE001 - fall back to the documented default
        return 50000


def _preview_rows() -> int:
    try:
        return int(get_app_config().connectors.result_preview_rows)
    except Exception:  # noqa: BLE001 - fall back to the documented default
        return 5


def _retention_hours() -> int:
    """Return the spill-file retention window in hours (0 = keep forever)."""
    try:
        return int(get_app_config().connectors.result_spill_retention_hours)
    except Exception:  # noqa: BLE001 - fall back to the documented default
        return 24


def _prune_spill_files(host_dir: Path, retention_hours: int) -> None:
    """Delete spilled data/metadata files older than the retention window."""
    if retention_hours <= 0:
        return
    cutoff = time.time() - retention_hours * 3600
    try:
        for entry in host_dir.iterdir():
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to prune spilled connector results under %s: %s", host_dir, exc)


def _read_file_limit_bytes() -> int:
    """Return the read_file pre-read size limit (0 = guard disabled)."""
    try:
        sandbox_cfg = get_app_config().sandbox
        return int(sandbox_cfg.read_file_max_bytes) if sandbox_cfg else 5 * 1024 * 1024
    except Exception:  # noqa: BLE001 - mirror the read_file tool fallback
        return 5 * 1024 * 1024


def _resolve_spill_target(context: ConnectorRuntimeContext) -> tuple[Path, str] | None:
    """Resolve (host dir, sandbox virtual dir) for a thread, or None if unavailable."""
    if not context.thread_id:
        return None
    try:
        host_dir = get_paths().sandbox_work_dir(context.thread_id, user_id=context.user_id) / SPILL_SUBDIR
        host_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 - spill must never break the tool call
        logger.warning("Connector result spill directory unavailable; returning result inline", exc_info=True)
        return None
    return host_dir, f"{VIRTUAL_PATH_PREFIX}/workspace/{SPILL_SUBDIR}"


def _column_names(columns: Any) -> list[str] | None:
    if not isinstance(columns, list) or not columns:
        return None
    names: list[str] = []
    for index, column in enumerate(columns):
        if isinstance(column, dict):
            name = column.get("name")
            names.append(str(name) if name is not None else f"column_{index}")
        else:
            names.append(str(column))
    return names


def _row_objects(rows: Any, names: list[str]) -> list[dict[str, Any]] | None:
    if not isinstance(rows, list):
        return None
    objects: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            objects.append(row)
        elif isinstance(row, (list, tuple)):
            objects.append({names[i]: (row[i] if i < len(row) else None) for i in range(len(names))})
        else:
            objects.append({names[0]: row} if names else {"value": row})
    return objects


def _write_result_files(
    payload: dict[str, Any],
    *,
    context: ConnectorRuntimeContext,
    target: tuple[Path, str],
    tool_name: str,
    connector_id: str,
    sql: str | None,
    tables: list[str] | None,
    extra_meta: dict[str, Any] | None,
    size_chars: int,
) -> dict[str, Any]:
    host_dir, virtual_dir = target
    _prune_spill_files(host_dir, _retention_hours())
    columns = _column_names(payload.get("columns"))
    rows = payload.get("rows")
    row_objects = _row_objects(rows, columns) if columns else None
    is_tabular = row_objects is not None

    raw_row_count = payload.get("row_count")
    returned = int(raw_row_count) if isinstance(raw_row_count, int) else (len(row_objects) if row_objects is not None else None)
    truncated = bool(payload.get("truncated"))
    total = None if (truncated or returned is None) else returned

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    safe_connector = _SAFE_NAME_RE.sub("_", connector_id).strip("_") or "connector"
    stem = f"{tool_name}-{safe_connector}-{stamp}-{digest}"
    suffix = ".ndjson" if is_tabular else ".json"
    host_file = host_dir / f"{stem}{suffix}"
    host_meta = host_dir / f"{stem}.meta.json"
    virtual_file = f"{virtual_dir}/{host_file.name}"
    virtual_meta = f"{virtual_dir}/{host_meta.name}"

    if is_tabular:
        with host_file.open("w", encoding="utf-8", newline="\n") as handle:
            for row in row_objects:
                handle.write(json.dumps(row, ensure_ascii=False, default=str))
                handle.write("\n")
    else:
        host_file.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")

    meta: dict[str, Any] = {
        "tool": tool_name,
        "connector_id": connector_id,
        "thread_id": context.thread_id,
        "run_id": context.run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "format": "ndjson" if is_tabular else "json",
        "file": virtual_file,
        "sql": sql,
        "tables": tables or [],
        "columns": columns,
        "row_count": returned,
        "returned": returned,
        "total": total,
        "truncated": truncated,
        "serialized_chars": size_chars,
        "elapsed_ms": payload.get("elapsed_ms"),
    }
    if extra_meta:
        meta.update(extra_meta)
    host_meta.write_text(json.dumps(meta, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

    if is_tabular:
        preview = row_objects[: _preview_rows()] if row_objects else []
        preview_payload: Any = preview
    else:
        preview_payload = json.dumps(payload, ensure_ascii=False, default=str)[:_PREVIEW_TEXT_CHARS]

    try:
        spill_file_bytes: int | None = host_file.stat().st_size
    except OSError:
        spill_file_bytes = None
    read_limit = _read_file_limit_bytes()
    if spill_file_bytes is not None and read_limit and spill_file_bytes > read_limit:
        # read_file refuses files above this limit before slicing, so pointing the
        # model at start_line/end_line would guarantee a failed tool call.
        spill_note = (
            f"The full result was written to {virtual_file} ({meta['format']}, {spill_file_bytes:,} bytes), "
            f"which exceeds the read_file limit of {read_limit:,} bytes. Do not call read_file on it; "
            "inspect or aggregate it with `bash`/`python` (for example `head`, `sed -n`, or a script)."
        )
    else:
        spill_note = (
            f"The full result was written to {virtual_file} ({meta['format']}). "
            "Read it with read_file using start_line/end_line; write a script that aggregates it "
            "instead of loading the whole file into the conversation."
        )

    summary: dict[str, Any] = {
        "spilled": True,
        "file": virtual_file,
        "meta_file": virtual_meta,
        "format": meta["format"],
        "total": total,
        "returned": returned,
        "truncated": truncated,
        "columns": columns,
        "preview": preview_payload,
        "note": spill_note,
    }
    if truncated:
        summary["data_complete"] = False
        summary["warning"] = _INCOMPLETE_WARNING
        summary["next_steps"] = list(_INCOMPLETE_NEXT_STEPS)
    elif is_tabular:
        summary["data_complete"] = True
    return summary


async def maybe_spill_result(
    payload: dict[str, Any],
    *,
    context: ConnectorRuntimeContext,
    service: Any,
    connector_id: str,
    tool_name: str,
    capability: str,
    operation: str,
    sql: str | None = None,
    tables: list[str] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return *payload* unchanged when small; otherwise persist it and return a summary."""
    threshold = _inline_max_chars()
    size_chars = serialized_size(payload)
    if size_chars <= threshold:
        return payload

    target = _resolve_spill_target(context)
    if target is None:
        # No thread workspace available: keep the legacy inline behaviour so the
        # caller still receives its data. The outbound request budget middleware
        # remains the last line of defence against an oversized model request.
        logger.warning(
            "Connector result too large (%s chars > %s) but no thread workspace is available; returning inline",
            size_chars,
            threshold,
        )
        return payload

    try:
        summary = _write_result_files(
            payload,
            context=context,
            target=target,
            tool_name=tool_name,
            connector_id=connector_id,
            sql=sql,
            tables=tables,
            extra_meta=extra_meta,
            size_chars=size_chars,
        )
    except Exception:  # noqa: BLE001 - never break the tool call because of disk issues
        logger.warning("Failed to spill connector result to disk; returning inline", exc_info=True)
        return payload

    record_spill = getattr(service, "record_result_spill", None)
    if record_spill is not None:
        try:
            await record_spill(
                connector_id,
                context=context,
                capability=capability,
                operation=operation,
                request_summary={
                    "tool": tool_name,
                    "sql_hash": hashlib.sha256((sql or "").encode("utf-8")).hexdigest() if sql else None,
                    "sql_preview": (sql or "")[:_SQL_PREVIEW_CHARS] or None,
                    "tables": tables or [],
                    "inline_max_chars": threshold,
                },
                result_summary={
                    "file": summary["file"],
                    "row_count": summary.get("returned"),
                    "returned": summary.get("returned"),
                    "total": summary.get("total"),
                    "truncated": summary.get("truncated"),
                    "serialized_chars": size_chars,
                },
            )
        except Exception:  # noqa: BLE001 - audit failure must not hide the result
            logger.warning("Failed to write connector result spill audit record", exc_info=True)

    return summary
