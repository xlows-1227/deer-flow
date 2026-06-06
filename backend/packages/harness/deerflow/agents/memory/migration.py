"""Legacy memory migration into the v2 profile format."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from deerflow.agents.memory.models import MemoryProfile, MemoryProfileItem, MemorySourceRef
from deerflow.agents.memory.safety import scrub_memory_text, should_drop_memory_text
from deerflow.agents.memory.storage import utc_now_iso_z
from deerflow.agents.memory.storage_v2 import MemoryStorageV2, get_memory_storage_v2
from deerflow.agents.memory.updater import get_memory_data
from deerflow.config.paths import get_paths


def migrate_legacy_memory(user_id: str, *, storage: MemoryStorageV2 | None = None, force: bool = False) -> MemoryProfile:
    """Backup legacy memory.json and create/update profile.json.

    The migration is idempotent. Existing v2 profile is preserved unless
    ``force`` is true.
    """
    storage = storage or get_memory_storage_v2()
    profile_path = storage.profile_file(user_id)
    if profile_path.exists() and not force:
        return storage.load_profile(user_id)

    legacy_path = get_paths().user_memory_file(user_id)
    backup_path = storage.legacy_memory_file(user_id)
    if legacy_path.exists() and not backup_path.exists():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_path, backup_path)

    legacy_data = get_memory_data(user_id=user_id)
    profile = legacy_memory_to_profile(user_id, legacy_data)
    profile = storage.save_profile(user_id, profile)
    storage.append_source_event(
        user_id,
        eventType="migrated",
        targetType="profile_item",
        targetId="legacy-memory",
        sourceKind="legacy",
        metadata={"legacyPath": str(Path(legacy_path))},
    )
    return profile


def legacy_memory_to_profile(user_id: str, legacy_data: dict[str, Any]) -> MemoryProfile:
    """Convert old user/history/facts shape into the v2 profile shape."""
    now = utc_now_iso_z()
    legacy_ref = MemorySourceRef(type="legacy", id="legacy-memory")

    def item(item_type: str, content: str, confidence: float) -> MemoryProfileItem | None:
        cleaned = scrub_memory_text(content)
        if should_drop_memory_text(cleaned):
            return None
        import hashlib

        digest = hashlib.sha1(f"legacy:{item_type}:{cleaned.casefold()}".encode()).hexdigest()[:12]
        return MemoryProfileItem(
            id=f"profile_{item_type}_{digest}",
            type=item_type,  # type: ignore[arg-type]
            content=cleaned,
            confidence=confidence,
            sourceRefs=[legacy_ref],
            createdAt=now,
            updatedAt=now,
        )

    profile = MemoryProfile(personId=user_id, updatedAt=now)
    user_data = legacy_data.get("user", {}) if isinstance(legacy_data, dict) else {}
    work_summary = user_data.get("workContext", {}).get("summary", "")
    personal_summary = user_data.get("personalContext", {}).get("summary", "")
    top_summary = user_data.get("topOfMind", {}).get("summary", "")
    overview_parts = [scrub_memory_text(p) for p in (work_summary, personal_summary) if p]
    profile.overview = " ".join(p for p in overview_parts if p and not should_drop_memory_text(p))
    if top_summary_item := item("top_of_mind", top_summary, 0.55):
        profile.topOfMind.append(top_summary_item)

    facts = legacy_data.get("facts", []) if isinstance(legacy_data, dict) else []
    for fact in facts if isinstance(facts, list) else []:
        if not isinstance(fact, dict):
            continue
        content = fact.get("content")
        if not isinstance(content, str):
            continue
        category = str(fact.get("category", "profile"))
        confidence = float(fact.get("confidence", 0.6) or 0.6)
        target = {
            "preference": "preference",
            "behavior": "skill_usage",
            "knowledge": "interest",
            "goal": "top_of_mind",
            "correction": "correction",
            "context": "profile",
        }.get(category, "profile")
        converted = item(target, content, confidence)
        if converted is None:
            continue
        if target == "preference":
            profile.preferences.append(converted)
        elif target == "skill_usage":
            profile.skillUsagePatterns.append(converted)
        elif target == "interest":
            profile.interests.append(converted)
        elif target == "top_of_mind":
            profile.topOfMind.append(converted)
        elif target == "correction":
            profile.corrections.append(converted)
        else:
            # v2 has no generic profile bucket in the prompt model; keep stable
            # context in interests so it remains visible but low-risk.
            profile.interests.append(converted.model_copy(update={"type": "interest"}))
    return profile
