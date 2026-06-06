"""Domain models for the v2 user-level memory system."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryItemType = Literal[
    "interest",
    "preference",
    "profile",
    "communication_style",
    "skill_usage",
    "top_of_mind",
    "correction",
]


class MemorySourceRef(BaseModel):
    """Reference to the evidence that supports a memory item."""

    type: Literal["daily", "legacy", "manual"] = "daily"
    id: str


class MemoryProfileItem(BaseModel):
    """A single durable memory profile item."""

    id: str
    type: MemoryItemType
    content: str
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    sourceRefs: list[MemorySourceRef] = Field(default_factory=list)
    createdAt: str = ""
    updatedAt: str = ""
    status: Literal["active", "inactive"] = "active"


class MemorySuppression(BaseModel):
    """A user-controlled rule that prevents active injection/mention."""

    id: str
    scope: Literal["profile_item", "topic", "daily"]
    targetId: str
    reason: str = "user_do_not_mention"
    createdAt: str = ""
    createdBy: str = "user"


class DailyPersonSummary(BaseModel):
    """Reviewable per-user, per-day memory evidence."""

    version: str = "1.0"
    id: str
    personId: str
    date: str
    timezone: str = "UTC"
    summary: str = ""
    interests: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    profileSignals: list[str] = Field(default_factory=list)
    recentFocus: list[str] = Field(default_factory=list)
    skillUsagePatterns: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    sourceThreads: list[str] = Field(default_factory=list)
    sourceRuns: list[str] = Field(default_factory=list)
    status: Literal["active", "deleted"] = "active"
    deletedAt: str | None = None
    updatedAt: str = ""

class MemoryProfile(BaseModel):
    """Prompt-facing durable memory profile."""

    version: str = "1.0"
    personId: str
    updatedAt: str = ""
    overview: str = ""
    interests: list[MemoryProfileItem] = Field(default_factory=list)
    preferences: list[MemoryProfileItem] = Field(default_factory=list)
    communicationStyle: list[MemoryProfileItem] = Field(default_factory=list)
    skillUsagePatterns: list[MemoryProfileItem] = Field(default_factory=list)
    topOfMind: list[MemoryProfileItem] = Field(default_factory=list)
    corrections: list[MemoryProfileItem] = Field(default_factory=list)
    suppressions: list[MemorySuppression] = Field(default_factory=list)

    def iter_items(self) -> list[MemoryProfileItem]:
        """Return all profile items in injection order."""
        return [
            *self.corrections,
            *self.preferences,
            *self.communicationStyle,
            *self.skillUsagePatterns,
            *self.interests,
            *self.topOfMind,
        ]


class MemorySourceEvent(BaseModel):
    """Append-only source/audit event."""

    eventId: str
    eventType: Literal["created", "updated", "deleted", "restored", "purged", "suppressed", "migrated", "manual"]
    targetType: Literal["daily", "profile_item", "suppression"]
    targetId: str
    userId: str
    threadId: str | None = None
    runId: str | None = None
    sourceKind: Literal["rollup", "manual", "scheduled", "legacy"] = "rollup"
    createdAt: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRollupInput(BaseModel):
    """Sanitized conversation input stored for daily rollup."""

    id: str
    userId: str
    date: str
    threadId: str
    runId: str | None = None
    messages: list[dict[str, str]] = Field(default_factory=list)
    createdAt: str = ""
    updatedAt: str = ""


class MemoryConsolidationResult(BaseModel):
    """Result metadata from rebuilding a profile."""

    profile: MemoryProfile
    sourceDailyIds: list[str] = Field(default_factory=list)
    updatedAt: str = ""
