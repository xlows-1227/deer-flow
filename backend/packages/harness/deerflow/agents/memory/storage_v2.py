"""File storage for the v2 daily-person memory architecture."""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.agents.memory.models import (
    DailyPersonSummary,
    MemoryProfile,
    MemoryRollupInput,
    MemorySourceEvent,
)
from deerflow.agents.memory.storage import utc_now_iso_z
from deerflow.config.paths import get_paths


def local_date_from_utc(dt: datetime | None = None) -> str:
    """Return a YYYY-MM-DD date string. First version uses UTC as stable fallback."""
    return (dt or datetime.now(UTC)).date().isoformat()


def daily_summary_id(user_id: str, date: str) -> str:
    """Stable id for one user's daily summary."""
    return f"daily_{date}_{user_id}"


class MemoryStorageV2:
    """User-level memory v2 file storage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def memory_dir(self, user_id: str) -> Path:
        return get_paths().user_dir(user_id) / "memory"

    def profile_file(self, user_id: str) -> Path:
        return self.memory_dir(user_id) / "profile.json"

    def daily_dir(self, user_id: str) -> Path:
        return self.memory_dir(user_id) / "daily"

    def daily_file(self, user_id: str, date: str) -> Path:
        return self.daily_dir(user_id) / f"{date}.json"

    def sources_file(self, user_id: str) -> Path:
        return self.memory_dir(user_id) / "sources.jsonl"

    def tombstones_file(self, user_id: str) -> Path:
        return self.memory_dir(user_id) / "tombstones.jsonl"

    def rollup_inputs_dir(self, user_id: str) -> Path:
        return self.memory_dir(user_id) / "rollup-inputs"

    def rollup_input_file(self, user_id: str, date: str, thread_id: str) -> Path:
        return self.rollup_inputs_dir(user_id) / date / f"{thread_id}.json"

    def legacy_memory_file(self, user_id: str) -> Path:
        return self.memory_dir(user_id) / "legacy-memory.json"

    def load_profile(self, user_id: str) -> MemoryProfile:
        data = self._load_json(self.profile_file(user_id), default=None)
        if isinstance(data, dict):
            return MemoryProfile(**data)
        return MemoryProfile(personId=user_id, updatedAt=utc_now_iso_z())

    def save_profile(self, user_id: str, profile: MemoryProfile) -> MemoryProfile:
        profile = profile.model_copy(update={"updatedAt": utc_now_iso_z()})
        self._atomic_write_json(self.profile_file(user_id), profile.model_dump(mode="json"))
        return profile

    def load_daily(self, user_id: str, date: str, *, include_deleted: bool = False) -> DailyPersonSummary | None:
        data = self._load_json(self.daily_file(user_id, date), default=None)
        if not isinstance(data, dict):
            return None
        summary = DailyPersonSummary(**data)
        if summary.status == "deleted" and not include_deleted:
            return None
        return summary

    def save_daily(self, user_id: str, summary: DailyPersonSummary) -> DailyPersonSummary:
        summary = summary.model_copy(update={"updatedAt": utc_now_iso_z()})
        self._atomic_write_json(self.daily_file(user_id, summary.date), summary.model_dump(mode="json"))
        return summary

    def list_daily(self, user_id: str, *, limit: int | None = None, include_deleted: bool = False) -> list[DailyPersonSummary]:
        ddir = self.daily_dir(user_id)
        if not ddir.exists():
            return []
        summaries: list[DailyPersonSummary] = []
        for path in sorted(ddir.glob("*.json"), reverse=True):
            data = self._load_json(path, default=None)
            if not isinstance(data, dict):
                continue
            summary = DailyPersonSummary(**data)
            if summary.status == "deleted" and not include_deleted:
                continue
            summaries.append(summary)
            if limit is not None and len(summaries) >= limit:
                break
        return summaries

    def soft_delete_daily(self, user_id: str, date: str) -> DailyPersonSummary | None:
        summary = self.load_daily(user_id, date, include_deleted=True)
        if summary is None:
            return None
        summary = summary.model_copy(update={"status": "deleted", "deletedAt": utc_now_iso_z()})
        summary = self.save_daily(user_id, summary)
        self.append_source_event(
            user_id,
            eventType="deleted",
            targetType="daily",
            targetId=summary.id,
            sourceKind="manual",
        )
        return summary

    def restore_daily(self, user_id: str, date: str) -> DailyPersonSummary | None:
        summary = self.load_daily(user_id, date, include_deleted=True)
        if summary is None:
            return None
        summary = summary.model_copy(update={"status": "active", "deletedAt": None})
        summary = self.save_daily(user_id, summary)
        self.append_source_event(
            user_id,
            eventType="restored",
            targetType="daily",
            targetId=summary.id,
            sourceKind="manual",
        )
        return summary

    def purge_daily(self, user_id: str, date: str) -> bool:
        path = self.daily_file(user_id, date)
        if not path.exists():
            return False
        existing = self.load_daily(user_id, date, include_deleted=True)
        path.unlink()
        if existing is not None:
            self.append_source_event(
                user_id,
                eventType="purged",
                targetType="daily",
                targetId=existing.id,
                sourceKind="manual",
            )
        return True

    def clear_user_memory(self, user_id: str) -> None:
        """Remove all persisted memory owned by a user, including legacy files."""
        memory_dir = self.memory_dir(user_id)
        if memory_dir.exists():
            shutil.rmtree(memory_dir)

        paths = get_paths()
        legacy_user_memory = paths.user_memory_file(user_id)
        if legacy_user_memory.exists():
            legacy_user_memory.unlink()

        legacy_agents_dir = paths.user_agents_dir(user_id)
        if legacy_agents_dir.exists():
            for legacy_agent_memory in legacy_agents_dir.glob("*/memory.json"):
                legacy_agent_memory.unlink()

    def save_rollup_input(self, user_id: str, rollup_input: MemoryRollupInput) -> MemoryRollupInput:
        rollup_input = rollup_input.model_copy(update={"updatedAt": utc_now_iso_z()})
        self._atomic_write_json(
            self.rollup_input_file(user_id, rollup_input.date, rollup_input.threadId),
            rollup_input.model_dump(mode="json"),
        )
        return rollup_input

    def load_rollup_inputs(self, user_id: str, date: str, *, thread_id: str | None = None) -> list[MemoryRollupInput]:
        if thread_id is not None:
            paths = [self.rollup_input_file(user_id, date, thread_id)]
        else:
            directory = self.rollup_inputs_dir(user_id) / date
            paths = sorted(directory.glob("*.json")) if directory.exists() else []
        inputs: list[MemoryRollupInput] = []
        for path in paths:
            data = self._load_json(path, default=None)
            if isinstance(data, dict):
                inputs.append(MemoryRollupInput(**data))
        return inputs

    def list_rollup_targets(self) -> list[tuple[str, str]]:
        """Return (user_id, date) pairs that have pending rollup inputs."""
        users_dir = get_paths().base_dir / "users"
        if not users_dir.exists():
            return []
        targets: list[tuple[str, str]] = []
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir():
                continue
            inputs_root = user_dir / "memory" / "rollup-inputs"
            if not inputs_root.exists():
                continue
            for date_dir in inputs_root.iterdir():
                if date_dir.is_dir() and any(date_dir.glob("*.json")):
                    targets.append((user_dir.name, date_dir.name))
        return sorted(set(targets))

    def append_source_event(
        self,
        user_id: str,
        *,
        eventType: Any,
        targetType: Any,
        targetId: str,
        sourceKind: Any,
        threadId: str | None = None,
        runId: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemorySourceEvent:
        event = MemorySourceEvent(
            eventId=f"src_{uuid.uuid4().hex[:12]}",
            eventType=eventType,
            targetType=targetType,
            targetId=targetId,
            userId=user_id,
            threadId=threadId,
            runId=runId,
            sourceKind=sourceKind,
            createdAt=utc_now_iso_z(),
            metadata=metadata or {},
        )
        self._append_jsonl(self.sources_file(user_id), event.model_dump(mode="json"))
        return event

    def _load_json(self, path: Path, *, default: Any) -> Any:
        try:
            if not path.exists():
                return default
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _atomic_write_json(self, path: Path, data: dict[str, Any]) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temp_path.replace(path)

    def _append_jsonl(self, path: Path, data: dict[str, Any]) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")


_storage_v2_instance: MemoryStorageV2 | None = None
_storage_v2_lock = threading.Lock()


def get_memory_storage_v2() -> MemoryStorageV2:
    """Return process-wide v2 storage."""
    global _storage_v2_instance
    if _storage_v2_instance is not None:
        return _storage_v2_instance
    with _storage_v2_lock:
        if _storage_v2_instance is None:
            _storage_v2_instance = MemoryStorageV2()
    return _storage_v2_instance
