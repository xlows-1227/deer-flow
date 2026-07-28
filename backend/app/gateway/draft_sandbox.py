"""Trusted draft-sandbox thread metadata and context reconstruction."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from deerflow.publishing.context import DraftSandboxContext
from deerflow.publishing.draft_service import DraftService

DRAFT_SANDBOX_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "draft_sandbox",
        "draft_sandbox_agent_id",
        "draft_sandbox_revision",
        "draft_sandbox_billable",
    }
)


def build_draft_sandbox_context(
    *,
    owner_user_id: str,
    agent_id: str,
    agent: dict[str, Any],
    draft: dict[str, Any],
) -> DraftSandboxContext:
    """Build the immutable authority injected into one sandbox Run."""
    return DraftSandboxContext(
        owner_user_id=owner_user_id,
        agent_id=agent_id,
        agent_slug=str(agent["slug"]),
        draft_revision=int(draft["revision"]),
        description=str(agent.get("description") or ""),
        agent_markdown=str(draft.get("agent_markdown") or ""),
        soul_markdown=str(draft.get("soul_markdown") or ""),
        model_name=(str(draft["model_name"]) if draft.get("model_name") is not None else None),
        tool_groups=tuple(str(value) for value in draft.get("tool_groups") or ()),
        skill_names=tuple(str(entry["skill_name"]) for entry in draft.get("skills") or ()),
        connector_capabilities=tuple(
            sorted(
                {
                    (
                        str(entry["connector_instance_id"]),
                        str(entry["capability"]),
                    )
                    for entry in draft.get("connector_grants") or ()
                }
            )
        ),
    )


def draft_sandbox_thread_metadata(
    *,
    agent_id: str,
    draft_revision: int,
) -> dict[str, Any]:
    """Return server-owned metadata that identifies a sandbox conversation."""
    return {
        "draft_sandbox": True,
        "draft_sandbox_agent_id": agent_id,
        "draft_sandbox_revision": draft_revision,
        "draft_sandbox_billable": False,
    }


async def resolve_draft_sandbox_context(
    *,
    thread_store,
    draft_service: DraftService | None,
    owner_user_id: str | None,
    thread_id: str,
) -> DraftSandboxContext | None:
    """Restore sandbox authority from trusted Thread metadata.

    Draft revisions are monotonic. Re-reading the current row reproduces the
    original snapshot only while its revision still matches. A changed draft
    therefore fails closed and requires a new sandbox conversation.
    """
    record = await thread_store.get(thread_id)
    if record is None:
        return None
    metadata = record.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("draft_sandbox") is not True:
        return None
    if metadata.get("draft_sandbox_billable") is not False:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invalid_draft_sandbox_thread",
                "message": "Draft sandbox metadata is invalid.",
            },
        )
    if owner_user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if draft_service is None:
        raise HTTPException(
            status_code=503,
            detail="Published-agent service not available",
        )

    agent_id = metadata.get("draft_sandbox_agent_id")
    expected_revision = metadata.get("draft_sandbox_revision")
    if not isinstance(agent_id, str) or not agent_id or isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invalid_draft_sandbox_thread",
                "message": "Draft sandbox metadata is invalid.",
            },
        )

    agent = await draft_service.get_agent(
        agent_id,
        owner_user_id=owner_user_id,
    )
    draft = await draft_service.get_draft(
        agent_id,
        owner_user_id=owner_user_id,
    )
    if agent is None or draft is None:
        raise HTTPException(status_code=404, detail="Draft sandbox not found")
    current_revision = int(draft["revision"])
    if current_revision != expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "draft_sandbox_revision_stale",
                "message": ("The draft changed after this sandbox conversation started. Start a new sandbox conversation."),
                "draft_revision": expected_revision,
                "current_revision": current_revision,
            },
        )
    return build_draft_sandbox_context(
        owner_user_id=owner_user_id,
        agent_id=agent_id,
        agent=agent,
        draft=draft,
    )
