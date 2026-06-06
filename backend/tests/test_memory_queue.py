import threading
import time
from unittest.mock import MagicMock, patch

from deerflow.agents.memory.queue import ConversationContext, MemoryUpdateQueue
from deerflow.config.memory_config import MemoryConfig


def _memory_config(**overrides: object) -> MemoryConfig:
    config = MemoryConfig()
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_queue_add_preserves_existing_correction_flag_for_same_thread() -> None:
    queue = MemoryUpdateQueue()

    with (
        patch("deerflow.agents.memory.queue.get_memory_config", return_value=_memory_config(enabled=True)),
        patch.object(queue, "_reset_timer"),
    ):
        queue.add(thread_id="thread-1", messages=["first"], correction_detected=True)
        queue.add(thread_id="thread-1", messages=["second"], correction_detected=False)

    assert len(queue._queue) == 1
    assert queue._queue[0].messages == ["second"]
    assert queue._queue[0].correction_detected is True


def test_process_queue_captures_rollup_input_with_correction_context() -> None:
    queue = MemoryUpdateQueue()
    queue._queue = [
        ConversationContext(
            thread_id="thread-1",
            messages=["conversation"],
            agent_name="lead_agent",
            correction_detected=True,
        )
    ]
    captured = MagicMock(return_value=object())

    with patch("deerflow.agents.memory.capture.capture_rollup_input", captured):
        queue._process_queue()

    captured.assert_called_once_with(
        user_id="default",
        thread_id="thread-1",
        messages=["conversation"],
    )


def test_queue_add_preserves_existing_reinforcement_flag_for_same_thread() -> None:
    queue = MemoryUpdateQueue()

    with (
        patch("deerflow.agents.memory.queue.get_memory_config", return_value=_memory_config(enabled=True)),
        patch.object(queue, "_reset_timer"),
    ):
        queue.add(thread_id="thread-1", messages=["first"], reinforcement_detected=True)
        queue.add(thread_id="thread-1", messages=["second"], reinforcement_detected=False)

    assert len(queue._queue) == 1
    assert queue._queue[0].messages == ["second"]
    assert queue._queue[0].reinforcement_detected is True


def test_process_queue_captures_rollup_input_with_reinforcement_context() -> None:
    queue = MemoryUpdateQueue()
    queue._queue = [
        ConversationContext(
            thread_id="thread-1",
            messages=["conversation"],
            agent_name="lead_agent",
            reinforcement_detected=True,
        )
    ]
    captured = MagicMock(return_value=object())

    with patch("deerflow.agents.memory.capture.capture_rollup_input", captured):
        queue._process_queue()

    captured.assert_called_once_with(
        user_id="default",
        thread_id="thread-1",
        messages=["conversation"],
    )


def test_flush_nowait_cancels_existing_timer_and_starts_immediate_timer() -> None:
    queue = MemoryUpdateQueue()
    existing_timer = MagicMock()
    queue._timer = existing_timer
    created_timer = MagicMock()

    with patch("deerflow.agents.memory.queue.threading.Timer", return_value=created_timer) as timer_cls:
        queue.flush_nowait()

    existing_timer.cancel.assert_called_once_with()
    timer_cls.assert_called_once_with(0, queue._process_queue)
    assert created_timer.daemon is True
    created_timer.start.assert_called_once_with()
    assert queue._timer is created_timer


def test_add_nowait_cancels_existing_timer_and_starts_immediate_timer() -> None:
    queue = MemoryUpdateQueue()
    existing_timer = MagicMock()
    queue._timer = existing_timer
    created_timer = MagicMock()

    with (
        patch("deerflow.agents.memory.queue.get_memory_config", return_value=_memory_config(enabled=True)),
        patch("deerflow.agents.memory.queue.threading.Timer", return_value=created_timer) as timer_cls,
    ):
        queue.add_nowait(thread_id="thread-1", messages=["conversation"], agent_name="lead-agent")

    existing_timer.cancel.assert_called_once_with()
    timer_cls.assert_called_once_with(0, queue._process_queue)
    assert queue.pending_count == 1
    assert queue._queue[0].agent_name == "lead-agent"
    assert created_timer.daemon is True
    created_timer.start.assert_called_once_with()


def test_process_queue_reschedules_immediately_when_already_processing() -> None:
    queue = MemoryUpdateQueue()
    queue._processing = True
    created_timer = MagicMock()

    with patch("deerflow.agents.memory.queue.threading.Timer", return_value=created_timer) as timer_cls:
        queue._process_queue()

    timer_cls.assert_called_once_with(0, queue._process_queue)
    assert created_timer.daemon is True
    created_timer.start.assert_called_once_with()


def test_flush_nowait_is_non_blocking() -> None:
    queue = MemoryUpdateQueue()
    started = threading.Event()
    finished = threading.Event()

    def _slow_process_queue() -> None:
        started.set()
        time.sleep(0.2)
        finished.set()

    queue._process_queue = _slow_process_queue

    start = time.perf_counter()
    queue.flush_nowait()
    elapsed = time.perf_counter() - start

    assert started.wait(0.1) is True
    assert elapsed < 0.1
    assert finished.is_set() is False
    assert finished.wait(1.0) is True


def test_queue_coalesces_updates_for_different_agents_in_same_thread() -> None:
    queue = MemoryUpdateQueue()

    with (
        patch("deerflow.agents.memory.queue.get_memory_config", return_value=_memory_config(enabled=True)),
        patch.object(queue, "_reset_timer"),
    ):
        queue.add(thread_id="thread-1", messages=["agent-a"], agent_name="agent-a")
        queue.add(thread_id="thread-1", messages=["agent-b"], agent_name="agent-b")

    assert queue.pending_count == 1
    assert queue._queue[0].messages == ["agent-b"]


def test_queue_still_coalesces_updates_for_same_agent_in_same_thread() -> None:
    queue = MemoryUpdateQueue()

    with (
        patch("deerflow.agents.memory.queue.get_memory_config", return_value=_memory_config(enabled=True)),
        patch.object(queue, "_reset_timer"),
    ):
        queue.add(
            thread_id="thread-1",
            messages=["first"],
            agent_name="agent-a",
            correction_detected=True,
        )
        queue.add(
            thread_id="thread-1",
            messages=["second"],
            agent_name="agent-a",
            correction_detected=False,
        )

    assert queue.pending_count == 1
    assert queue._queue[0].agent_name == "agent-a"
    assert queue._queue[0].messages == ["second"]
    assert queue._queue[0].correction_detected is True


def test_process_queue_captures_user_level_memory_once_for_different_agents() -> None:
    queue = MemoryUpdateQueue()

    with (
        patch("deerflow.agents.memory.queue.get_memory_config", return_value=_memory_config(enabled=True)),
        patch.object(queue, "_reset_timer"),
    ):
        queue.add(thread_id="thread-1", messages=["agent-a"], agent_name="agent-a")
        queue.add(thread_id="thread-1", messages=["agent-b"], agent_name="agent-b")

    captured = MagicMock(return_value=object())

    with (
        patch("deerflow.agents.memory.capture.capture_rollup_input", captured),
        patch("deerflow.agents.memory.queue.time.sleep"),
    ):
        queue.flush()

    captured.assert_called_once_with(
        user_id="default",
        thread_id="thread-1",
        messages=["agent-b"],
    )


def test_flush_user_processes_only_target_users_pending_contexts() -> None:
    queue = MemoryUpdateQueue()
    queue._queue = [
        ConversationContext(thread_id="thread-a", messages=["a"], user_id="user-a"),
        ConversationContext(thread_id="thread-b", messages=["b"], user_id="user-b"),
    ]
    captured = MagicMock(return_value=object())

    with patch("deerflow.agents.memory.capture.capture_rollup_input", captured):
        queue.flush_user("user-a")

    captured.assert_called_once_with(user_id="user-a", thread_id="thread-a", messages=["a"])
    assert queue.pending_count == 1
    assert queue._queue[0].user_id == "user-b"
