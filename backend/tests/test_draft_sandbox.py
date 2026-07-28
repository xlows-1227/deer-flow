from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.gateway.draft_sandbox import resolve_draft_sandbox_context


class _ThreadStore:
    def __init__(self, metadata: dict | None) -> None:
        self.metadata = metadata

    async def get(self, thread_id: str):
        if self.metadata is None:
            return None
        return {"thread_id": thread_id, "metadata": self.metadata}


class _DraftService:
    def __init__(self, *, revision: int = 3) -> None:
        self.revision = revision

    async def get_agent(self, agent_id: str, *, owner_user_id: str):
        if agent_id != "agent-1" or owner_user_id != "owner-a":
            return None
        return {
            "id": agent_id,
            "slug": "scope-agent",
            "description": "Scoped sandbox",
        }

    async def get_draft(self, agent_id: str, *, owner_user_id: str):
        if agent_id != "agent-1" or owner_user_id != "owner-a":
            return None
        return {
            "agent_id": agent_id,
            "revision": self.revision,
            "agent_markdown": "Frozen instruction",
            "soul_markdown": "Frozen soul",
            "model_name": "model-a",
            "tool_groups": ["web"],
            "skills": [
                {"skill_name": "selected-skill", "source": "public"},
            ],
            "connector_grants": [],
        }


def _metadata() -> dict:
    return {
        "draft_sandbox": True,
        "draft_sandbox_agent_id": "agent-1",
        "draft_sandbox_revision": 3,
        "draft_sandbox_billable": False,
    }


@pytest.mark.asyncio
async def test_resolve_draft_sandbox_context_restores_exact_capability_scope():
    context = await resolve_draft_sandbox_context(
        thread_store=_ThreadStore(_metadata()),
        draft_service=_DraftService(),
        owner_user_id="owner-a",
        thread_id="thread-1",
    )

    assert context is not None
    assert context.agent_slug == "scope-agent"
    assert context.draft_revision == 3
    assert context.skill_names == ("selected-skill",)
    assert context.connector_ids == ()
    assert context.connector_capability_map() == {}


@pytest.mark.asyncio
async def test_resolve_draft_sandbox_context_fails_closed_after_draft_changes():
    with pytest.raises(HTTPException) as exc_info:
        await resolve_draft_sandbox_context(
            thread_store=_ThreadStore(_metadata()),
            draft_service=_DraftService(revision=4),
            owner_user_id="owner-a",
            thread_id="thread-1",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "draft_sandbox_revision_stale"


@pytest.mark.asyncio
async def test_resolve_draft_sandbox_context_ignores_ordinary_threads():
    context = await resolve_draft_sandbox_context(
        thread_store=_ThreadStore({"agent_name": "scope-agent"}),
        draft_service=None,
        owner_user_id=None,
        thread_id="thread-1",
    )

    assert context is None
