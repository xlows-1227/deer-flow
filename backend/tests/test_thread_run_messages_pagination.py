"""Tests for paginated GET /api/threads/{thread_id}/runs/{run_id}/messages endpoint."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import thread_runs
from deerflow.runtime import DisconnectMode, RunManager, RunRecord, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(event_store=None, run_manager=None):
    """Build a test FastAPI app with stub auth and mocked state."""
    app = make_authed_test_app()
    app.include_router(thread_runs.router)

    if event_store is not None:
        app.state.run_event_store = event_store
    if run_manager is not None:
        app.state.run_manager = run_manager

    return app


def _make_event_store(rows: list[dict]):
    """Return an AsyncMock event store whose list_messages_by_run() returns rows."""
    store = MagicMock()
    store.list_messages_by_run = AsyncMock(return_value=rows)
    return store


def _make_message(seq: int) -> dict:
    return {"seq": seq, "event_type": "ai_message", "category": "message", "content": f"msg-{seq}"}


def _make_raw_skill_rows() -> list[dict]:
    return [
        {
            "seq": 1,
            "run_id": "run-skill",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {
                "type": "ai",
                "content": "",
                "tool_calls": [
                    {
                        "name": "read_file",
                        "args": {"path": "/mnt/skills/public/demo/SKILL.md"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            },
            "metadata": {},
        },
        {
            "seq": 2,
            "run_id": "run-skill",
            "event_type": "llm.tool.result",
            "category": "message",
            "content": {
                "type": "tool",
                "name": "read_file",
                "tool_call_id": "call-1",
                "content": "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE",
                "additional_kwargs": {},
            },
            "metadata": {},
        },
    ]


def _make_store_only_run_manager() -> RunManager:
    store = MemoryRunStore()
    asyncio.run(
        store.put(
            "store-only-run",
            thread_id="thread-store",
            assistant_id="lead_agent",
            status="running",
            multitask_strategy="reject",
            metadata={},
            kwargs={},
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    return RunManager(store=store)


def test_returns_paginated_envelope():
    """GET /api/threads/{tid}/runs/{rid}/messages returns {data: [...], has_more: bool}."""
    rows = [_make_message(i) for i in range(1, 4)]
    app = _make_app(event_store=_make_event_store(rows))
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/messages")
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    assert "has_more" in body
    assert body["has_more"] is False
    assert len(body["data"]) == 3


def test_has_more_true_when_extra_row_returned():
    """has_more=True when event store returns limit+1 rows."""
    # Default limit is 50; provide 51 rows
    rows = [_make_message(i) for i in range(1, 52)]  # 51 rows
    app = _make_app(event_store=_make_event_store(rows))
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-2/runs/run-2/messages")
    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is True
    assert len(body["data"]) == 50  # trimmed to limit


def test_after_seq_forwarded_to_event_store():
    """after_seq query param is forwarded to event_store.list_messages_by_run."""
    rows = [_make_message(10)]
    event_store = _make_event_store(rows)
    app = _make_app(event_store=event_store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-3/runs/run-3/messages?after_seq=5")
    assert response.status_code == 200
    event_store.list_messages_by_run.assert_awaited_once_with(
        "thread-3",
        "run-3",
        limit=51,  # default limit(50) + 1
        before_seq=None,
        after_seq=5,
    )


def test_before_seq_forwarded_to_event_store():
    """before_seq query param is forwarded to event_store.list_messages_by_run."""
    rows = [_make_message(3)]
    event_store = _make_event_store(rows)
    app = _make_app(event_store=event_store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-4/runs/run-4/messages?before_seq=10")
    assert response.status_code == 200
    event_store.list_messages_by_run.assert_awaited_once_with(
        "thread-4",
        "run-4",
        limit=51,
        before_seq=10,
        after_seq=None,
    )


def test_custom_limit_forwarded_to_event_store():
    """Custom limit is forwarded as limit+1 to the event store."""
    rows = [_make_message(i) for i in range(1, 6)]
    event_store = _make_event_store(rows)
    app = _make_app(event_store=event_store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-5/runs/run-5/messages?limit=10")
    assert response.status_code == 200
    event_store.list_messages_by_run.assert_awaited_once_with(
        "thread-5",
        "run-5",
        limit=11,  # 10 + 1
        before_seq=None,
        after_seq=None,
    )


def test_empty_data_when_no_messages():
    """Returns empty data list with has_more=False when no messages exist."""
    app = _make_app(event_store=_make_event_store([]))
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-6/runs/run-6/messages")
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["has_more"] is False


def test_run_messages_redacts_legacy_skill_results_at_read_time():
    rows = _make_raw_skill_rows()
    app = _make_app(event_store=_make_event_store(rows))

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-skill/messages")

    assert response.status_code == 200
    body = response.json()
    assert "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE" not in response.text
    assert body["data"][1]["content"]["content"] == "Skill instructions loaded."
    assert body["data"][1]["metadata"]["skill_execution"]["skill_name"] == "demo"


def test_run_events_redacts_legacy_skill_results_at_read_time():
    rows = _make_raw_skill_rows()
    event_store = _make_event_store([])
    event_store.list_events = AsyncMock(return_value=rows)
    app = _make_app(event_store=event_store)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-skill/events")

    assert response.status_code == 200
    assert "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE" not in response.text


def test_thread_messages_redacts_skill_results_across_runs():
    rows = _make_raw_skill_rows()
    event_store = _make_event_store([])
    event_store.list_messages = AsyncMock(return_value=rows)
    feedback_repo = MagicMock()
    feedback_repo.list_by_thread_grouped = AsyncMock(return_value={})
    app = _make_app(event_store=event_store)
    app.state.feedback_repo = feedback_repo

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages")

    assert response.status_code == 200
    assert "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE" not in response.text


def test_run_messages_loads_legacy_call_context_across_page_boundary():
    tool_row = {
        "seq": 20,
        "run_id": "run-skill",
        "event_type": "llm.tool.result",
        "category": "message",
        "content": {
            "type": "tool",
            "name": "bash",
            "tool_call_id": "call-bash",
            "content": "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE",
            "additional_kwargs": {},
        },
        "metadata": {},
    }
    call_row = {
        "seq": 10,
        "run_id": "run-skill",
        "event_type": "llm.ai.response",
        "category": "message",
        "content": {
            "type": "ai",
            "content": "",
            "tool_calls": [
                {
                    "name": "bash",
                    "args": {"command": "cat /mnt/skills/custom/demo/SKILL.md"},
                    "id": "call-bash",
                    "type": "tool_call",
                }
            ],
        },
        "metadata": {},
    }
    event_store = MagicMock()
    event_store.list_messages_by_run = AsyncMock(side_effect=[[tool_row], [call_row]])
    app = _make_app(event_store=event_store)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-skill/messages")

    assert response.status_code == 200
    assert "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE" not in response.text
    assert event_store.list_messages_by_run.await_count == 2


def test_run_messages_preserves_non_skill_read_file_across_page_boundary():
    tool_row = {
        "seq": 20,
        "run_id": "run-file",
        "event_type": "llm.tool.result",
        "category": "message",
        "content": {
            "type": "tool",
            "name": "read_file",
            "tool_call_id": "call-file",
            "content": "ordinary user file contents",
            "additional_kwargs": {},
        },
        "metadata": {},
    }
    call_row = {
        "seq": 10,
        "run_id": "run-file",
        "event_type": "llm.ai.response",
        "category": "message",
        "content": {
            "type": "ai",
            "content": "",
            "tool_calls": [
                {
                    "name": "read_file",
                    "args": {"path": "/mnt/user-data/workspace/report.txt"},
                    "id": "call-file",
                    "type": "tool_call",
                }
            ],
        },
        "metadata": {},
    }
    event_store = MagicMock()
    event_store.list_messages_by_run = AsyncMock(side_effect=[[tool_row], [call_row]])
    app = _make_app(event_store=event_store)

    app_config = SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills"))
    with (
        patch("app.gateway.skill_redaction.get_app_config", return_value=app_config),
        TestClient(app) as client,
    ):
        response = client.get("/api/threads/thread-1/runs/run-file/messages")

    assert response.status_code == 200
    assert response.json()["data"][0]["content"]["content"] == "ordinary user file contents"
    assert event_store.list_messages_by_run.await_count == 2


def test_run_messages_fail_closed_when_legacy_call_context_is_missing():
    tool_row = {
        "seq": 20,
        "run_id": "run-skill",
        "event_type": "llm.tool.result",
        "category": "message",
        "content": {
            "type": "tool",
            "name": "bash",
            "tool_call_id": "missing-call",
            "content": "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE",
            "additional_kwargs": {},
        },
        "metadata": {},
    }
    event_store = MagicMock()
    event_store.list_messages_by_run = AsyncMock(side_effect=[[tool_row], []])
    app = _make_app(event_store=event_store)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-skill/messages")

    assert response.status_code == 200
    assert "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE" not in response.text
    assert response.json()["data"][0]["content"]["content"] == "Skill instructions loaded."


def test_get_run_hydrates_store_only_run():
    """GET /api/threads/{tid}/runs/{rid} should read historical store rows."""
    app = _make_app(run_manager=_make_store_only_run_manager())
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-store/runs/store-only-run")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "store-only-run"
    assert body["thread_id"] == "thread-store"
    assert body["status"] == "running"


def test_cancel_store_only_run_returns_409():
    """Store-only runs are readable but not cancellable by this worker."""
    app = _make_app(run_manager=_make_store_only_run_manager())
    with TestClient(app) as client:
        response = client.post("/api/threads/thread-store/runs/store-only-run/cancel")

    assert response.status_code == 409
    assert "not active on this worker" in response.json()["detail"]


def test_join_store_only_run_returns_409():
    """join endpoint should return 409 for store-only runs (no local stream state)."""
    app = _make_app(run_manager=_make_store_only_run_manager())
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-store/runs/store-only-run/join")

    assert response.status_code == 409
    assert "not active on this worker" in response.json()["detail"]


def test_stream_store_only_run_returns_409():
    """stream endpoint (action=None) should return 409 for store-only runs."""
    app = _make_app(run_manager=_make_store_only_run_manager())
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-store/runs/store-only-run/stream")

    assert response.status_code == 409
    assert "not active on this worker" in response.json()["detail"]


def test_list_runs_forwards_limit_and_offset():
    """GET /api/threads/{tid}/runs forwards pagination to RunManager."""
    record = RunRecord(
        run_id="run-page",
        thread_id="thread-page",
        assistant_id="lead_agent",
        status=RunStatus.success,
        on_disconnect=DisconnectMode.cancel,
        created_at="2026-01-02T00:00:00+00:00",
        updated_at="2026-01-02T00:00:01+00:00",
    )
    run_manager = MagicMock()
    run_manager.list_by_thread = AsyncMock(return_value=[record])
    app = _make_app(run_manager=run_manager)
    app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-page"})

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-page/runs?limit=2&offset=3")

    assert response.status_code == 200
    body = response.json()
    assert [run["run_id"] for run in body] == ["run-page"]
    run_manager.list_by_thread.assert_awaited_once()
    _, kwargs = run_manager.list_by_thread.await_args
    assert kwargs["limit"] == 2
    assert kwargs["offset"] == 3


def test_list_runs_unauthenticated_returns_401():
    """Unauthenticated callers must not receive an empty run list."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(thread_runs.router)
    with TestClient(app) as client:
        response = client.get("/api/threads/0000/runs?limit=10&offset=0")

    assert response.status_code == 401


def test_list_runs_missing_thread_returns_404():
    """Unknown threads must 404 instead of returning []."""
    from langgraph.store.memory import InMemoryStore

    from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore

    app = make_authed_test_app()
    app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())
    app.include_router(thread_runs.router)
    with TestClient(app) as client:
        response = client.get("/api/threads/0000/runs?limit=10&offset=0")

    assert response.status_code == 404
