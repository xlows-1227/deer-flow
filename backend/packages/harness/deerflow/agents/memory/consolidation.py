"""Build prompt-facing memory profiles from daily summaries."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable

from deerflow.agents.memory.models import (
    DailyPersonSummary,
    MemoryProfile,
    MemoryProfileItem,
    MemorySourceRef,
)
from deerflow.agents.memory.safety import sanitize_memory_list
from deerflow.agents.memory.storage import utc_now_iso_z
from deerflow.agents.memory.storage_v2 import MemoryStorageV2, get_memory_storage_v2


def _stable_item_id(item_type: str, content: str) -> str:
    digest = hashlib.sha1(f"{item_type}:{content.casefold()}".encode()).hexdigest()[:12]
    return f"profile_{item_type}_{digest}"


def _make_item(item_type: str, content: str, source_ids: Iterable[str], *, confidence: float = 0.8) -> MemoryProfileItem:
    now = utc_now_iso_z()
    return MemoryProfileItem(
        id=_stable_item_id(item_type, content),
        type=item_type,  # type: ignore[arg-type]
        content=content,
        confidence=confidence,
        sourceRefs=[MemorySourceRef(type="daily", id=source_id) for source_id in sorted(set(source_ids))],
        createdAt=now,
        updatedAt=now,
    )


class ProfileConsolidator:
    """Consolidate active daily summaries into a durable user profile."""

    def __init__(self, storage: MemoryStorageV2 | None = None):
        self._storage = storage or get_memory_storage_v2()

    def rebuild_profile(self, user_id: str) -> MemoryProfile:
        """Rebuild and persist a user's profile from active daily summaries."""
        daily_summaries = self._storage.list_daily(user_id, include_deleted=False)
        existing = self._storage.load_profile(user_id)
        profile = build_profile_from_daily(user_id, daily_summaries)
        _preserve_manual_items(profile, existing)
        return self._storage.save_profile(user_id, profile)


def build_profile_from_daily(user_id: str, daily_summaries: list[DailyPersonSummary]) -> MemoryProfile:
    """Pure consolidation logic used by production and tests."""
    grouped: dict[str, dict[str, set[str]]] = {
        "interest": defaultdict(set),
        "preference": defaultdict(set),
        "profile": defaultdict(set),
        "skill_usage": defaultdict(set),
        "top_of_mind": defaultdict(set),
        "correction": defaultdict(set),
    }

    overview_parts: list[str] = []
    for daily in daily_summaries:
        if daily.status == "deleted":
            continue
        if daily.summary:
            overview_parts.append(daily.summary)
        for value in sanitize_memory_list(daily.interests):
            grouped["interest"][value].add(daily.id)
        for value in sanitize_memory_list(daily.preferences):
            grouped["preference"][value].add(daily.id)
        for value in sanitize_memory_list(daily.profileSignals):
            grouped["profile"][value].add(daily.id)
        for value in sanitize_memory_list(daily.skillUsagePatterns):
            grouped["skill_usage"][value].add(daily.id)
        for value in sanitize_memory_list(daily.recentFocus):
            grouped["top_of_mind"][value].add(daily.id)
        for value in sanitize_memory_list(daily.corrections):
            grouped["correction"][value].add(daily.id)

    def make_items(item_type: str, *, confidence: float = 0.8, limit: int | None = None) -> list[MemoryProfileItem]:
        ordered = sorted(grouped[item_type].items(), key=lambda entry: (len(entry[1]), entry[0]), reverse=True)
        if limit is not None:
            ordered = ordered[:limit]
        return [_make_item(item_type, content, source_ids, confidence=confidence) for content, source_ids in ordered]

    overview = " ".join(sanitize_memory_list(overview_parts[:3]))
    return MemoryProfile(
        personId=user_id,
        updatedAt=utc_now_iso_z(),
        overview=overview,
        interests=make_items("interest", confidence=0.75),
        preferences=make_items("preference", confidence=0.85),
        skillUsagePatterns=make_items("skill_usage", confidence=0.8),
        topOfMind=make_items("top_of_mind", confidence=0.65, limit=10),
        corrections=make_items("correction", confidence=0.95),
    )


def _preserve_manual_items(profile: MemoryProfile, existing: MemoryProfile) -> None:
    """Keep user-authored profile items when daily evidence is rebuilt."""
    for attr in (
        "interests",
        "preferences",
        "communicationStyle",
        "skillUsagePatterns",
        "topOfMind",
        "corrections",
    ):
        generated = getattr(profile, attr)
        generated_ids = {item.id for item in generated}
        manual_items = [
            item
            for item in getattr(existing, attr)
            if item.status == "active"
            and any(ref.type == "manual" for ref in item.sourceRefs)
            and item.id not in generated_ids
        ]
        generated.extend(manual_items)
