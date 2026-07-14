from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from starlette.requests import Request

from app.gateway.routers import shares

_SECRET_SKILL_MARKER = "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE"


class _FakeSession:
    async def get(self, model, token):
        return SimpleNamespace(
            share_token=token,
            thread_id="thread-shared-skill",
            expires_at=None,
        )


class _FakeSessionContext:
    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeSessionFactory:
    def __call__(self):
        return _FakeSessionContext()


class _FakeCheckpointer:
    async def aget_tuple(self, config):
        return SimpleNamespace(
            checkpoint={
                "channel_values": {
                    "title": "Shared thread",
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "read_file",
                                    "args": {"path": "/mnt/skills/private-skill/SKILL.md"},
                                    "id": "skill-call-1",
                                }
                            ],
                        ),
                        ToolMessage(
                            content=f"# Private instructions\n{_SECRET_SKILL_MARKER}",
                            tool_call_id="skill-call-1",
                        ),
                    ],
                }
            },
            metadata={"created_at": "2026-07-13T00:00:00+00:00"},
        )


@pytest.mark.asyncio
async def test_public_share_hides_skill_instruction_content():
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace())})

    with (
        patch.object(shares, "get_session_factory", return_value=_FakeSessionFactory()),
        patch.object(shares, "get_checkpointer", return_value=_FakeCheckpointer()),
    ):
        response = await shares.get_shared_thread("share-token", request)

    serialized = response.model_dump_json()
    assert _SECRET_SKILL_MARKER not in serialized
    assert "/mnt/skills/private-skill/SKILL.md" not in serialized
    assert "Skill instructions loaded." in serialized
