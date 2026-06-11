"""Prompt injection selection for the v2 memory architecture."""

from __future__ import annotations

from deerflow.agents.memory.models import DailyPersonSummary, MemoryProfile, MemoryProfileItem
from deerflow.agents.memory.prompt import _count_tokens
from deerflow.agents.memory.safety import is_high_risk_memory_text, sanitize_memory_list
from deerflow.agents.memory.storage_v2 import MemoryStorageV2, get_memory_storage_v2


def _format_item(prefix: str, item: MemoryProfileItem) -> str | None:
    if item.status != "active" or is_high_risk_memory_text(item.content):
        return None
    return f"- {prefix}: {item.content}"


def format_profile_for_injection(profile: MemoryProfile, *, max_tokens: int = 2000) -> str:
    """Format a v2 profile for prompt injection."""
    sections: list[str] = []
    if profile.overview and not is_high_risk_memory_text(profile.overview):
        sections.append("用户画像概览:\n" + profile.overview)

    lines: list[str] = []
    for item in profile.corrections:
        if line := _format_item("需要避免", item):
            lines.append(line)
    for item in profile.preferences:
        if line := _format_item("偏好", item):
            lines.append(line)
    for item in profile.communicationStyle:
        if line := _format_item("沟通风格", item):
            lines.append(line)
    for item in profile.skillUsagePatterns:
        if line := _format_item("使用习惯", item):
            lines.append(line)
    for item in profile.interests:
        if line := _format_item("兴趣/画像", item):
            lines.append(line)
    for item in profile.topOfMind:
        if line := _format_item("近期关注", item):
            lines.append(line)
    if lines:
        sections.append("长期记忆:\n" + "\n".join(lines))

    result = "\n\n".join(sections).strip()
    if _count_tokens(result) <= max_tokens:
        return result
    # Keep corrections/preferences first by truncating formatted lines.
    kept: list[str] = []
    running = 0
    for line in result.splitlines():
        tokens = _count_tokens(line + "\n")
        if running + tokens > max_tokens:
            break
        kept.append(line)
        running += tokens
    return "\n".join(kept).strip()


def select_daily_snippets(daily_summaries: list[DailyPersonSummary], *, max_snippets: int = 3) -> list[str]:
    """Select recent, safe daily snippets for injection."""
    snippets: list[str] = []
    for daily in sorted(daily_summaries, key=lambda d: d.date, reverse=True):
        if daily.status == "deleted":
            continue
        candidates = [
            *daily.preferences,
            *daily.skillUsagePatterns,
            *daily.recentFocus,
            *daily.interests,
        ]
        for value in sanitize_memory_list(candidates):
            snippets.append(f"- {daily.date}: {value}")
            if len(snippets) >= max_snippets:
                return snippets
    return snippets


def format_memory_v2_for_injection(
    user_id: str,
    *,
    max_tokens: int = 2000,
    storage: MemoryStorageV2 | None = None,
) -> str:
    """Load and format v2 memory for prompt injection."""
    storage = storage or get_memory_storage_v2()
    profile = storage.load_profile(user_id)
    profile_text = format_profile_for_injection(profile, max_tokens=max_tokens)
    remaining_tokens = max(0, max_tokens - _count_tokens(profile_text))
    daily_text = ""
    if remaining_tokens > 100:
        snippets = select_daily_snippets(storage.list_daily(user_id, limit=7), max_snippets=3)
        if snippets:
            candidate = "近期每日记忆片段:\n" + "\n".join(snippets)
            if _count_tokens(candidate) <= remaining_tokens:
                daily_text = candidate
    return "\n\n".join(part for part in (profile_text, daily_text) if part.strip())
