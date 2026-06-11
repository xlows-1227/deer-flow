"""Compatibility helpers between v2 memory and the legacy API shape."""

from __future__ import annotations

import hashlib

from deerflow.agents.memory.models import MemoryProfile, MemoryProfileItem, MemorySourceRef
from deerflow.agents.memory.safety import scrub_memory_text, should_drop_memory_text
from deerflow.agents.memory.storage import create_empty_memory, utc_now_iso_z
from deerflow.agents.memory.storage_v2 import MemoryStorageV2, get_memory_storage_v2

_PROFILE_ATTRS = [
    "interests",
    "preferences",
    "communicationStyle",
    "skillUsagePatterns",
    "topOfMind",
    "corrections",
]


def profile_to_legacy_memory(profile: MemoryProfile) -> dict:
    """Return a legacy MemoryResponse-compatible dict from a v2 profile."""
    memory = create_empty_memory()
    memory["lastUpdated"] = profile.updatedAt
    memory["user"]["workContext"] = {"summary": profile.overview, "updatedAt": profile.updatedAt}
    memory["user"]["personalContext"] = {"summary": _join_items([*profile.interests, *profile.preferences]), "updatedAt": profile.updatedAt}
    memory["user"]["topOfMind"] = {"summary": _join_items(profile.topOfMind), "updatedAt": profile.updatedAt}
    memory["history"]["recentMonths"] = {"summary": _join_items(profile.skillUsagePatterns), "updatedAt": profile.updatedAt}
    memory["history"]["earlierContext"] = {"summary": "", "updatedAt": ""}
    memory["history"]["longTermBackground"] = {"summary": _join_items(profile.communicationStyle), "updatedAt": profile.updatedAt}
    facts = []
    for item in profile.iter_items():
        if item.status != "active":
            continue
        facts.append(
            {
                "id": item.id,
                "content": item.content,
                "category": _legacy_category(item.type),
                "confidence": item.confidence,
                "createdAt": item.createdAt,
                "source": ",".join(ref.id for ref in item.sourceRefs) or "v2-profile",
            }
        )
    memory["facts"] = facts
    return memory


def add_manual_profile_item(
    content: str,
    category: str = "context",
    confidence: float = 0.5,
    *,
    user_id: str,
    storage: MemoryStorageV2 | None = None,
) -> MemoryProfile:
    """Create a profile item for legacy fact-create compatibility."""
    storage = storage or get_memory_storage_v2()
    cleaned = scrub_memory_text(content)
    if should_drop_memory_text(cleaned):
        raise ValueError("content")
    profile = storage.load_profile(user_id)
    item_type = _profile_type(category)
    now = utc_now_iso_z()
    digest = hashlib.sha1(f"manual:{item_type}:{cleaned.casefold()}".encode()).hexdigest()[:12]
    item = MemoryProfileItem(
        id=f"profile_{item_type}_{digest}",
        type=item_type,  # type: ignore[arg-type]
        content=cleaned,
        confidence=confidence,
        sourceRefs=[MemorySourceRef(type="manual", id="manual")],
        createdAt=now,
        updatedAt=now,
    )
    _bucket(profile, item_type).append(item)
    profile = storage.save_profile(user_id, profile)
    storage.append_source_event(
        user_id,
        eventType="manual",
        targetType="profile_item",
        targetId=item.id,
        sourceKind="manual",
    )
    return profile


def update_profile_item(
    fact_id: str,
    *,
    user_id: str,
    content: str | None = None,
    category: str | None = None,
    confidence: float | None = None,
    storage: MemoryStorageV2 | None = None,
) -> MemoryProfile:
    """Update a v2 profile item via the legacy fact PATCH API."""
    storage = storage or get_memory_storage_v2()
    profile = storage.load_profile(user_id)
    found = False
    for attr in _PROFILE_ATTRS:
        items = getattr(profile, attr)
        for index, item in enumerate(items):
            if item.id != fact_id:
                continue
            found = True
            update = {"updatedAt": utc_now_iso_z()}
            if content is not None:
                cleaned = scrub_memory_text(content)
                if should_drop_memory_text(cleaned):
                    raise ValueError("content")
                update["content"] = cleaned
            if confidence is not None:
                update["confidence"] = confidence
            new_item = item.model_copy(update=update)
            if category is not None:
                new_type = _profile_type(category)
                new_item = new_item.model_copy(update={"type": new_type})
                items.pop(index)
                _bucket(profile, new_type).append(new_item)
            else:
                items[index] = new_item
            break
        if found:
            break
    if not found:
        raise KeyError(fact_id)
    return storage.save_profile(user_id, profile)


def delete_profile_item(
    fact_id: str,
    *,
    user_id: str,
    storage: MemoryStorageV2 | None = None,
) -> MemoryProfile:
    """Delete a v2 profile item via the legacy fact DELETE API."""
    storage = storage or get_memory_storage_v2()
    profile = storage.load_profile(user_id)
    found = False
    for attr in _PROFILE_ATTRS:
        items = getattr(profile, attr)
        next_items = [item for item in items if item.id != fact_id]
        if len(next_items) != len(items):
            setattr(profile, attr, next_items)
            found = True
            break
    if not found:
        raise KeyError(fact_id)
    profile = storage.save_profile(user_id, profile)
    storage.append_source_event(
        user_id,
        eventType="deleted",
        targetType="profile_item",
        targetId=fact_id,
        sourceKind="manual",
    )
    return profile


def _join_items(items: list[MemoryProfileItem]) -> str:
    return " ".join(item.content for item in items if item.status == "active")


def _legacy_category(item_type: str) -> str:
    return {
        "interest": "knowledge",
        "preference": "preference",
        "communication_style": "preference",
        "skill_usage": "behavior",
        "top_of_mind": "goal",
        "correction": "correction",
    }.get(item_type, "context")


def _profile_type(category: str) -> str:
    return {
        "preference": "preference",
        "behavior": "skill_usage",
        "knowledge": "interest",
        "goal": "top_of_mind",
        "correction": "correction",
        "context": "interest",
    }.get(category, "interest")


def _bucket(profile: MemoryProfile, item_type: str) -> list[MemoryProfileItem]:
    return {
        "interest": profile.interests,
        "preference": profile.preferences,
        "communication_style": profile.communicationStyle,
        "skill_usage": profile.skillUsagePatterns,
        "top_of_mind": profile.topOfMind,
        "correction": profile.corrections,
    }.get(item_type, profile.interests)
