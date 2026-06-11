"""Daily rollup service for the v2 memory system."""

from __future__ import annotations

import json
from typing import Any

from deerflow.agents.memory.models import DailyPersonSummary, MemoryRollupInput
from deerflow.agents.memory.prompt import DAILY_ROLLUP_PROMPT
from deerflow.agents.memory.safety import sanitize_memory_list, scrub_memory_text
from deerflow.agents.memory.storage import utc_now_iso_z
from deerflow.agents.memory.storage_v2 import (
    MemoryStorageV2,
    daily_summary_id,
    get_memory_storage_v2,
    local_date_from_utc,
)
from deerflow.config.memory_config import get_memory_config
from deerflow.models import create_chat_model


class DailyRollupService:
    """Generate daily summaries from stored rollup inputs."""

    def __init__(self, storage: MemoryStorageV2 | None = None, model_name: str | None = None):
        self._storage = storage or get_memory_storage_v2()
        self._model_name = model_name

    def rollup_date(self, user_id: str, date: str | None = None, *, source_kind: str = "scheduled") -> DailyPersonSummary | None:
        """Roll up all inputs for a user's date."""
        date = date or local_date_from_utc()
        inputs = self._storage.load_rollup_inputs(user_id, date)
        return self._rollup_inputs(user_id, date, inputs, source_kind=source_kind)

    def rollup_thread(self, user_id: str, thread_id: str, date: str | None = None) -> DailyPersonSummary | None:
        """Roll up a single thread into that day's summary."""
        date = date or local_date_from_utc()
        inputs = self._storage.load_rollup_inputs(user_id, date, thread_id=thread_id)
        return self._rollup_inputs(user_id, date, inputs, source_kind="manual")

    def rollup_thread_incremental(
        self,
        user_id: str,
        thread_id: str,
        date: str | None = None,
    ) -> DailyPersonSummary | None:
        """Merge one thread's summary into the existing daily memory."""
        date = date or local_date_from_utc()
        inputs = self._storage.load_rollup_inputs(user_id, date, thread_id=thread_id)
        if not inputs:
            return None

        existing = self._storage.load_daily(user_id, date, include_deleted=True)
        if existing is not None and existing.status == "active" and thread_id in existing.sourceThreads:
            return existing

        payload = self._summarize(user_id, date, inputs)
        if not _has_memory_payload(payload):
            return None
        thread_summary = self._summary_from_payload(user_id, date, payload, inputs, existing=None)
        summary = _merge_daily_summaries(existing if existing and existing.status == "active" else None, thread_summary)
        summary = self._storage.save_daily(user_id, summary)
        self._storage.append_source_event(
            user_id,
            eventType="updated" if existing is not None else "created",
            targetType="daily",
            targetId=summary.id,
            sourceKind="manual",
            threadId=thread_id,
        )
        return summary

    def _rollup_inputs(
        self,
        user_id: str,
        date: str,
        inputs: list[MemoryRollupInput],
        *,
        source_kind: str,
    ) -> DailyPersonSummary | None:
        if not inputs:
            return None
        payload = self._summarize(user_id, date, inputs)
        if not _has_memory_payload(payload):
            return None
        existing = self._storage.load_daily(user_id, date, include_deleted=True)
        summary = self._summary_from_payload(user_id, date, payload, inputs, existing=existing)
        summary = self._storage.save_daily(user_id, summary)
        source_threads = summary.sourceThreads
        for thread_id in source_threads or [None]:
            self._storage.append_source_event(
                user_id,
                eventType="updated" if existing is not None else "created",
                targetType="daily",
                targetId=summary.id,
                sourceKind=source_kind,
                threadId=thread_id,
            )
        return summary

    def _summary_from_payload(
        self,
        user_id: str,
        date: str,
        payload: dict[str, Any],
        inputs: list[MemoryRollupInput],
        *,
        existing: DailyPersonSummary | None,
    ) -> DailyPersonSummary:
        source_threads = sorted({i.threadId for i in inputs if i.threadId})
        source_runs = sorted({i.runId for i in inputs if i.runId})
        return DailyPersonSummary(
            version="1.0",
            id=existing.id if existing is not None else daily_summary_id(user_id, date),
            personId=user_id,
            date=date,
            timezone="UTC",
            summary=scrub_memory_text(str(payload.get("summary", ""))),
            interests=sanitize_memory_list(payload.get("interests", [])),
            preferences=sanitize_memory_list(payload.get("preferences", [])),
            profileSignals=sanitize_memory_list(payload.get("profileSignals", [])),
            recentFocus=sanitize_memory_list(payload.get("recentFocus", [])),
            skillUsagePatterns=sanitize_memory_list(payload.get("skillUsagePatterns", [])),
            corrections=sanitize_memory_list(payload.get("corrections", [])),
            sourceThreads=source_threads,
            sourceRuns=source_runs,
            status="active",
            deletedAt=None,
            updatedAt=utc_now_iso_z(),
        )

    def _summarize(self, user_id: str, date: str, inputs: list[MemoryRollupInput]) -> dict[str, Any]:
        conversation = "\n\n".join(message["content"] for item in inputs for message in item.messages if message.get("content"))
        prompt = DAILY_ROLLUP_PROMPT.format(user_id=user_id, date=date, conversation=conversation[:12000])
        config = get_memory_config()
        model_name = self._model_name or config.model_name
        try:
            model = create_chat_model(name=model_name, thinking_enabled=False)
            response = model.invoke(prompt, config={"run_name": "daily_memory_rollup"})
            text = response.content if isinstance(response.content, str) else str(response.content)
            if text.startswith("```"):
                lines = text.splitlines()
                text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            # Fall back to deterministic extraction so manual rollup still works
            # in tests/local installs without configured model credentials.
            pass
        return _fallback_rollup(conversation)


def _merge_unique(*groups: list[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for value in sanitize_memory_list(item for group in groups for item in group):
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            values.append(value)
    return values


def _has_memory_payload(payload: dict[str, Any]) -> bool:
    """Require at least one structured memory signal before persisting."""
    return any(
        sanitize_memory_list(payload.get(field, []))
        for field in (
            "interests",
            "preferences",
            "profileSignals",
            "recentFocus",
            "skillUsagePatterns",
            "corrections",
        )
    )


def _merge_daily_summaries(
    existing: DailyPersonSummary | None,
    incoming: DailyPersonSummary,
) -> DailyPersonSummary:
    if existing is None:
        return incoming
    return incoming.model_copy(
        update={
            "id": existing.id,
            "summary": " ".join(_merge_unique([existing.summary], [incoming.summary])),
            "interests": _merge_unique(existing.interests, incoming.interests),
            "preferences": _merge_unique(existing.preferences, incoming.preferences),
            "profileSignals": _merge_unique(existing.profileSignals, incoming.profileSignals),
            "recentFocus": _merge_unique(existing.recentFocus, incoming.recentFocus),
            "skillUsagePatterns": _merge_unique(existing.skillUsagePatterns, incoming.skillUsagePatterns),
            "corrections": _merge_unique(existing.corrections, incoming.corrections),
            "sourceThreads": sorted(set(existing.sourceThreads + incoming.sourceThreads)),
            "sourceRuns": sorted(set(existing.sourceRuns + incoming.sourceRuns)),
            "status": "active",
            "deletedAt": None,
            "updatedAt": utc_now_iso_z(),
        }
    )


def _fallback_rollup(conversation: str) -> dict[str, Any]:
    cleaned = scrub_memory_text(conversation)
    preferences: list[str] = []
    skill_usage: list[str] = []
    recent_focus: list[str] = []
    lowered = cleaned.lower()
    if "中文" in cleaned or "chinese" in lowered:
        preferences.append("用户偏好使用中文沟通和保存文档。")
    if "skill" in lowered or "工具" in cleaned:
        skill_usage.append("用户关注 skill、工具或工作流的使用习惯。")
    if "memory" in lowered or "记忆" in cleaned:
        recent_focus.append("用户最近关注记忆系统和个性化体验。")
    return {
        "summary": "用户最近有新的交互活动，关注点和偏好已按安全边界抽象总结。",
        "interests": [],
        "preferences": preferences,
        "profileSignals": [],
        "recentFocus": recent_focus,
        "skillUsagePatterns": skill_usage,
        "corrections": [],
    }
