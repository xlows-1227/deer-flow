from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.memory_scheduler import _run_due_rollups_once
from app.gateway.routers import memory
from deerflow.agents.memory.capture import capture_rollup_input
from deerflow.agents.memory.compat import add_manual_profile_item, delete_profile_item, profile_to_legacy_memory
from deerflow.agents.memory.consolidation import ProfileConsolidator
from deerflow.agents.memory.migration import legacy_memory_to_profile
from deerflow.agents.memory.models import DailyPersonSummary
from deerflow.agents.memory.queue import MemoryUpdateQueue
from deerflow.agents.memory.rollup import DailyRollupService
from deerflow.agents.memory.selection import format_memory_v2_for_injection
from deerflow.agents.memory.storage_v2 import MemoryStorageV2
from deerflow.config.memory_config import MemoryConfig


class _Msg:
    def __init__(self, msg_type: str, content: str):
        self.type = msg_type
        self.content = content


def _mock_paths(tmp_path: Path):
    paths = MagicMock()
    paths.base_dir = tmp_path
    paths.user_dir.side_effect = lambda user_id: tmp_path / "users" / user_id
    paths.user_memory_file.side_effect = lambda user_id: tmp_path / "users" / user_id / "memory.json"
    paths.user_agents_dir.side_effect = lambda user_id: tmp_path / "users" / user_id / "agents"
    return paths


def test_v2_storage_soft_delete_excludes_daily_and_rebuilds_profile(tmp_path):
    storage = MemoryStorageV2()
    daily = DailyPersonSummary(
        id="daily_2026-06-05_user-a",
        personId="user-a",
        date="2026-06-05",
        preferences=["用户偏好中文文档。"],
    )

    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        storage.save_daily("user-a", daily)
        profile = ProfileConsolidator(storage=storage).rebuild_profile("user-a")
        assert profile.preferences

        storage.soft_delete_daily("user-a", "2026-06-05")
        profile = ProfileConsolidator(storage=storage).rebuild_profile("user-a")

    assert profile.preferences == []
    assert storage.list_daily("user-a") == []


def test_clear_user_memory_removes_v2_and_legacy_user_files(tmp_path):
    storage = MemoryStorageV2()
    paths = _mock_paths(tmp_path)
    legacy_user_memory = tmp_path / "users" / "user-a" / "memory.json"
    legacy_agent_memory = tmp_path / "users" / "user-a" / "agents" / "researcher" / "memory.json"

    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=paths):
        storage.save_daily(
            "user-a",
            DailyPersonSummary(id="daily_2026-06-05_user-a", personId="user-a", date="2026-06-05"),
        )
        legacy_user_memory.parent.mkdir(parents=True, exist_ok=True)
        legacy_user_memory.write_text("{}", encoding="utf-8")
        legacy_agent_memory.parent.mkdir(parents=True, exist_ok=True)
        legacy_agent_memory.write_text("{}", encoding="utf-8")

        storage.clear_user_memory("user-a")

    assert not (tmp_path / "users" / "user-a" / "memory").exists()
    assert not legacy_user_memory.exists()
    assert not legacy_agent_memory.exists()


def test_clear_user_removes_only_target_users_pending_memory():
    queue = MemoryUpdateQueue()
    with patch.object(queue, "_reset_timer"):
        queue.add("thread-a", [_Msg("human", "a")], user_id="user-a")
        queue.add("thread-b", [_Msg("human", "b")], user_id="user-b")

    queue.clear_user("user-a")

    assert queue.pending_count == 1
    assert queue._queue[0].user_id == "user-b"


def test_capture_rollup_input_scrubs_uploads_and_sensitive_metadata(tmp_path):
    storage = MemoryStorageV2()
    messages = [
        _Msg("human", "请分析 <uploaded_files>secret.csv C:\\tmp\\secret.csv</uploaded_files> 我偏好中文文档"),
        _Msg("ai", "好的"),
    ]

    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        captured = capture_rollup_input(
            user_id="user-a",
            thread_id="thread-a",
            date="2026-06-05",
            messages=messages,
            storage=storage,
        )

    assert captured is not None
    text = captured.messages[0]["content"]
    assert "uploaded_files" not in text
    assert "secret.csv" not in text
    assert "中文文档" in text


def test_capture_rollup_input_excludes_injected_memory_and_assistant_text(tmp_path):
    storage = MemoryStorageV2()
    messages = [
        _Msg(
            "human",
            "<system-reminder><memory>用户偏好 Markdown</memory></system-reminder>你好",
        ),
        _Msg("ai", "用户一直关注文档总结，并偏好 Markdown。"),
    ]

    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        captured = capture_rollup_input(
            user_id="user-a",
            thread_id="thread-a",
            date="2026-06-05",
            messages=messages,
            storage=storage,
        )

    assert captured is not None
    text = captured.messages[0]["content"]
    assert text == "User: 你好"
    assert "Markdown" not in text
    assert "Assistant" not in text


def test_capture_rollup_input_returns_none_without_user_evidence(tmp_path):
    storage = MemoryStorageV2()

    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        captured = capture_rollup_input(
            user_id="user-a",
            thread_id="thread-a",
            date="2026-06-05",
            messages=[_Msg("ai", "用户偏好 Markdown。")],
            storage=storage,
        )

    assert captured is None


def test_rollup_thread_creates_daily_summary_with_fallback(tmp_path):
    storage = MemoryStorageV2()
    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        capture_rollup_input(
            user_id="user-a",
            thread_id="thread-a",
            date="2026-06-05",
            messages=[_Msg("human", "我希望所有文档都用中文保存，也关注 memory 设计。"), _Msg("ai", "收到")],
            storage=storage,
        )
        with patch("deerflow.agents.memory.rollup.create_chat_model", side_effect=RuntimeError("no model")):
            summary = DailyRollupService(storage=storage).rollup_thread("user-a", "thread-a", "2026-06-05")

    assert summary is not None
    assert summary.preferences
    assert summary.recentFocus
    assert summary.sourceThreads == ["thread-a"]


def test_incremental_thread_rollup_preserves_other_threads_and_is_idempotent(tmp_path):
    storage = MemoryStorageV2()
    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        storage.save_daily(
            "user-a",
            DailyPersonSummary(
                id="daily_2026-06-05_user-a",
                personId="user-a",
                date="2026-06-05",
                summary="Existing summary.",
                preferences=["Existing preference"],
                sourceThreads=["thread-a"],
            ),
        )
        capture_rollup_input(
            user_id="user-a",
            thread_id="thread-b",
            date="2026-06-05",
            messages=[_Msg("human", "I prefer concise answers.")],
            storage=storage,
        )
        service = DailyRollupService(storage=storage)
        payload = {
            "summary": "New summary.",
            "interests": [],
            "preferences": ["Concise answers"],
            "profileSignals": [],
            "recentFocus": [],
            "skillUsagePatterns": [],
            "corrections": [],
        }
        with patch.object(service, "_summarize", return_value=payload) as summarize:
            first = service.rollup_thread_incremental("user-a", "thread-b", "2026-06-05")
            second = service.rollup_thread_incremental("user-a", "thread-b", "2026-06-05")

    assert first is not None
    assert second is not None
    assert first.summary == "Existing summary. New summary."
    assert first.preferences == ["Existing preference", "Concise answers"]
    assert first.sourceThreads == ["thread-a", "thread-b"]
    assert second == first
    summarize.assert_called_once()


def test_incremental_thread_rollup_does_not_persist_summary_only_payload(tmp_path):
    storage = MemoryStorageV2()
    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        capture_rollup_input(
            user_id="user-a",
            thread_id="thread-a",
            date="2026-06-05",
            messages=[_Msg("human", "你好")],
            storage=storage,
        )
        service = DailyRollupService(storage=storage)
        with patch.object(service, "_summarize", return_value={"summary": "用户简单问候。"}):
            summary = service.rollup_thread_incremental("user-a", "thread-a", "2026-06-05")

        persisted = storage.load_daily("user-a", "2026-06-05")

    assert summary is None
    assert persisted is None


def test_legacy_migration_marks_facts_as_legacy_and_scrubs_details():
    legacy = {
        "user": {
            "workContext": {"summary": "用户关注 DeerFlow。"},
            "personalContext": {"summary": "用户偏好中文。"},
            "topOfMind": {"summary": "用户最近关注记忆系统。"},
        },
        "facts": [
            {
                "content": "用户偏好中文文档。",
                "category": "preference",
                "confidence": 0.95,
            },
            {
                "content": "database host=db.internal:3306 password=secret",
                "category": "context",
                "confidence": 0.95,
            },
        ],
    }

    profile = legacy_memory_to_profile("user-a", legacy)

    assert profile.preferences[0].sourceRefs[0].type == "legacy"
    assert all("password" not in item.content for item in profile.iter_items())
    assert all("db.internal" not in item.content for item in profile.iter_items())


def test_legacy_fact_compat_writes_profile_items(tmp_path):
    storage = MemoryStorageV2()
    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        profile = add_manual_profile_item("用户偏好简洁回答。", "preference", 0.9, user_id="user-a", storage=storage)
        legacy = profile_to_legacy_memory(profile)
        fact_id = legacy["facts"][0]["id"]
        profile = delete_profile_item(fact_id, user_id="user-a", storage=storage)
        legacy = profile_to_legacy_memory(profile)

    assert legacy["facts"] == []


def test_profile_rebuild_preserves_manual_items(tmp_path):
    storage = MemoryStorageV2()
    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        add_manual_profile_item("用户偏好简洁回答。", "preference", 0.9, user_id="user-a", storage=storage)
        storage.save_daily(
            "user-a",
            DailyPersonSummary(
                id="daily_2026-06-05_user-a",
                personId="user-a",
                date="2026-06-05",
                recentFocus=["用户最近关注记忆系统。"],
            ),
        )
        profile = ProfileConsolidator(storage=storage).rebuild_profile("user-a")

    assert [item.content for item in profile.preferences] == ["用户偏好简洁回答。"]
    assert [item.content for item in profile.topOfMind] == ["用户最近关注记忆系统。"]


def test_v2_injection_uses_profile_and_daily_snippets(tmp_path):
    storage = MemoryStorageV2()
    with patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)):
        storage.save_daily(
            "user-a",
            DailyPersonSummary(
                id="daily_2026-06-05_user-a",
                personId="user-a",
                date="2026-06-05",
                preferences=["用户偏好中文文档。"],
                recentFocus=["用户最近关注记忆系统。"],
            ),
        )
        ProfileConsolidator(storage=storage).rebuild_profile("user-a")
        text = format_memory_v2_for_injection("user-a", max_tokens=1000, storage=storage)

    assert "长期记忆" in text
    assert "用户偏好中文文档" in text
    assert "近期每日记忆片段" in text


def test_v2_memory_profile_and_daily_routes_return_new_shapes(tmp_path):
    app = FastAPI()
    app.include_router(memory.router)
    storage = MemoryStorageV2()

    with (
        patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)),
        patch("app.gateway.routers.memory.get_memory_storage_v2", return_value=storage),
        patch("app.gateway.routers.memory.get_memory_config", return_value=MemoryConfig(v2_enabled=True, migrate_legacy_on_startup=False)),
    ):
        storage.save_daily(
            "test-user-autouse",
            DailyPersonSummary(
                id="daily_2026-06-05_test-user-autouse",
                personId="test-user-autouse",
                date="2026-06-05",
                preferences=["用户偏好中文文档。"],
            ),
        )
        ProfileConsolidator(storage=storage).rebuild_profile("test-user-autouse")
        with TestClient(app) as client:
            profile_response = client.get("/api/memory/profile")
            daily_response = client.get("/api/memory/daily?limit=5")

    assert profile_response.status_code == 200
    assert profile_response.json()["preferences"][0]["content"] == "用户偏好中文文档。"
    assert daily_response.status_code == 200
    assert daily_response.json()[0]["date"] == "2026-06-05"


def test_memory_scheduler_rolls_up_pending_inputs(tmp_path):
    storage = MemoryStorageV2()
    with (
        patch("deerflow.agents.memory.storage_v2.get_paths", return_value=_mock_paths(tmp_path)),
        patch("app.gateway.memory_scheduler.get_memory_storage_v2", return_value=storage),
        patch("deerflow.agents.memory.rollup.create_chat_model", side_effect=RuntimeError("no model")),
    ):
        capture_rollup_input(
            user_id="user-a",
            thread_id="thread-a",
            date="2026-06-05",
            messages=[_Msg("human", "我偏好中文文档，也在关注 memory 设计。"), _Msg("ai", "收到")],
            storage=storage,
        )
        _run_due_rollups_once()
        daily = storage.load_daily("user-a", "2026-06-05")
        profile = storage.load_profile("user-a")

    assert daily is not None
    assert daily.preferences
    assert profile.preferences
