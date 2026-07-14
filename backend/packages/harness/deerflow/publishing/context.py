"""Trusted, immutable context for one published-agent run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from deerflow.publishing.quota import EffectiveQuota


@dataclass(frozen=True)
class PublishedAgentContext:
    """Server-derived runtime authority for a single external run.

    The internal release id is intentionally carried only in this trusted
    object. Public serializers must never expose the context itself.
    """

    owner_user_id: str
    agent_id: str
    release_id: str
    source: Literal["api", "feishu"]
    credential_id: str
    external_actor: str
    conversation_scope: str
    skill_revision_ids: tuple[str, ...]
    connector_capabilities: tuple[tuple[str, str], ...]
    tool_groups: tuple[str, ...]
    model_name: str
    instructions: str
    effective_quota: EffectiveQuota | Any
    correlation_id: str
    idempotency_key: str | None
    allowed_tool_names: tuple[str, ...] | None = None
    memory_enabled: bool = False

    def __post_init__(self) -> None:
        if self.memory_enabled:
            raise ValueError("published agent runtime must be memory-free")
        if self.source not in {"api", "feishu"}:
            raise ValueError("published agent source must be api or feishu")
