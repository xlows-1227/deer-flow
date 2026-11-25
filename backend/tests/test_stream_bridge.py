"""Tests for the in-memory StreamBridge implementation."""

import asyncio
import re

import anyio
import pytest

from deerflow.runtime import END_SENTINEL, HEARTBEAT_SENTINEL, MemoryStreamBridge, make_stream_bridge
from deerflow.runtime.stream_bridge import memory as memory_module

# ---------------------------------------------------------------------------
# Unit tests for MemoryStreamBridge
# ---------------------------------------------------------------------------


@pytest.fixture
def bridge() -> MemoryStreamBridge:
    return MemoryStreamBridge(queue_maxsize=256)


@pytest.mark.anyio
async def test_publish_subscribe(bridge: MemoryStreamBridge):
    """Three events followed by end should be received in order."""
    run_id = "run-1"

    await bridge.publish(run_id, "metadata", {"run_id": run_id})
    await bridge.publish(run_id, "values", {"messages": []})
    await bridge.publish(run_id, "updates", {"step": 1})
    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(received) == 4
    assert received[0].event == "metadata"
    assert received[1].event == "values"
    assert received[2].event == "updates"
    assert received[3] is END_SENTINEL


@pytest.mark.anyio
async def test_heartbeat(bridge: MemoryStreamBridge):
    """When no events arrive within the heartbeat interval, yield a heartbeat."""
    run_id = "run-heartbeat"
    bridge._get_or_create_stream(run_id)  # ensure stream exists

    received = []

    async def consumer():
        async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
            received.append(entry)
            if entry is HEARTBEAT_SENTINEL:
                break

    await asyncio.wait_for(consumer(), timeout=2.0)
    assert len(received) == 1
    assert received[0] is HEARTBEAT_SENTINEL


@pytest.mark.anyio
async def test_cleanup(bridge: MemoryStreamBridge):
    """After cleanup, the run's stream/event log is removed."""
    run_id = "run-cleanup"
    await bridge.publish(run_id, "test", {})
    assert run_id in bridge._streams

    await bridge.cleanup(run_id)
    assert run_id not in bridge._streams
    assert run_id not in bridge._counters


@pytest.mark.anyio
async def test_history_is_bounded():
    """Retained history should be bounded by queue_maxsize."""
    bridge = MemoryStreamBridge(queue_maxsize=1)
    run_id = "run-bp"

    await bridge.publish(run_id, "first", {})
    await bridge.publish(run_id, "second", {})
    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(received) == 2
    assert received[0].event == "second"
    assert received[1] is END_SENTINEL


@pytest.mark.anyio
async def test_multiple_runs(bridge: MemoryStreamBridge):
    """Two different run_ids should not interfere with each other."""
    await bridge.publish("run-a", "event-a", {"a": 1})
    await bridge.publish("run-b", "event-b", {"b": 2})
    await bridge.publish_end("run-a")
    await bridge.publish_end("run-b")

    events_a = []
    async for entry in bridge.subscribe("run-a", heartbeat_interval=1.0):
        events_a.append(entry)
        if entry is END_SENTINEL:
            break

    events_b = []
    async for entry in bridge.subscribe("run-b", heartbeat_interval=1.0):
        events_b.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(events_a) == 2
    assert events_a[0].event == "event-a"
    assert events_a[0].data == {"a": 1}

    assert len(events_b) == 2
    assert events_b[0].event == "event-b"
    assert events_b[0].data == {"b": 2}


@pytest.mark.anyio
async def test_event_id_format(bridge: MemoryStreamBridge):
    """Event IDs should use timestamp-sequence format."""
    run_id = "run-id-format"
    await bridge.publish(run_id, "test", {"key": "value"})
    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    event = received[0]
    assert re.match(r"^\d+-\d+$", event.id), f"Expected timestamp-seq format, got {event.id}"


@pytest.mark.anyio
async def test_subscribe_replays_after_last_event_id(bridge: MemoryStreamBridge):
    """Reconnect should replay buffered events after the provided Last-Event-ID."""
    run_id = "run-replay"
    await bridge.publish(run_id, "metadata", {"run_id": run_id})
    await bridge.publish(run_id, "values", {"step": 1})
    await bridge.publish(run_id, "updates", {"step": 2})
    await bridge.publish_end(run_id)

    first_pass = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        first_pass.append(entry)
        if entry is END_SENTINEL:
            break

    received = []
    async for entry in bridge.subscribe(
        run_id,
        last_event_id=first_pass[0].id,
        heartbeat_interval=1.0,
    ):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["values", "updates"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_slow_subscriber_does_not_skip_after_buffer_trim():
    """A slow subscriber should continue from the correct absolute offset."""
    bridge = MemoryStreamBridge(queue_maxsize=2)
    run_id = "run-slow-subscriber"
    await bridge.publish(run_id, "e1", {"step": 1})
    await bridge.publish(run_id, "e2", {"step": 2})

    stream = bridge._streams[run_id]
    e1_id = stream.events[0].id
    assert stream.start_offset == 0

    await bridge.publish(run_id, "e3", {"step": 3})  # trims e1
    assert stream.start_offset == 1
    assert [entry.event for entry in stream.events] == ["e2", "e3"]

    resumed_after_e1 = []
    async for entry in bridge.subscribe(
        run_id,
        last_event_id=e1_id,
        heartbeat_interval=1.0,
    ):
        resumed_after_e1.append(entry)
        if len(resumed_after_e1) == 2:
            break

    assert [entry.event for entry in resumed_after_e1] == ["e2", "e3"]
    e2_id = resumed_after_e1[0].id

    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(
        run_id,
        last_event_id=e2_id,
        heartbeat_interval=1.0,
    ):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["e3"]
    assert received[-1] is END_SENTINEL


# ---------------------------------------------------------------------------
# Stream termination tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_end_terminates_even_when_history_is_full():
    """publish_end() should terminate subscribers without mutating retained history."""
    bridge = MemoryStreamBridge(queue_maxsize=2)
    run_id = "run-end-history-full"

    await bridge.publish(run_id, "event-1", {"n": 1})
    await bridge.publish(run_id, "event-2", {"n": 2})
    stream = bridge._streams[run_id]
    assert [entry.event for entry in stream.events] == ["event-1", "event-2"]

    await bridge.publish_end(run_id)
    assert [entry.event for entry in stream.events] == ["event-1", "event-2"]

    events = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
        events.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in events[:-1]] == ["event-1", "event-2"]
    assert events[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_publish_end_without_history_yields_end_immediately():
    """Subscribers should still receive END when a run completes without events."""
    bridge = MemoryStreamBridge(queue_maxsize=2)
    run_id = "run-end-empty"
    await bridge.publish_end(run_id)

    events = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
        events.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(events) == 1
    assert events[0] is END_SENTINEL


@pytest.mark.anyio
async def test_publish_end_preserves_history_when_space_available():
    """When history has spare capacity, publish_end should preserve prior events."""
    bridge = MemoryStreamBridge(queue_maxsize=10)
    run_id = "run-no-evict"

    await bridge.publish(run_id, "event-1", {"n": 1})
    await bridge.publish(run_id, "event-2", {"n": 2})
    await bridge.publish_end(run_id)

    events = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
        events.append(entry)
        if entry is END_SENTINEL:
            break

    # All events plus END should be present
    assert len(events) == 3
    assert events[0].event == "event-1"
    assert events[1].event == "event-2"
    assert events[2] is END_SENTINEL


@pytest.mark.anyio
async def test_concurrent_tasks_end_sentinel():
    """Multiple concurrent producer/consumer pairs should all terminate properly.

    Simulates the production scenario where multiple runs share a single
    bridge instance — each must receive its own END sentinel.
    """
    bridge = MemoryStreamBridge(queue_maxsize=4)
    num_runs = 4

    async def producer(run_id: str):
        for i in range(10):  # More events than queue capacity
            await bridge.publish(run_id, f"event-{i}", {"i": i})
        await bridge.publish_end(run_id)

    async def consumer(run_id: str) -> list:
        events = []
        async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
            events.append(entry)
            if entry is END_SENTINEL:
                return events
        return events  # pragma: no cover

    run_ids = [f"concurrent-{i}" for i in range(num_runs)]
    results: dict[str, list] = {}

    async def consume_into(run_id: str) -> None:
        results[run_id] = await consumer(run_id)

    with anyio.fail_after(10):
        async with anyio.create_task_group() as task_group:
            for run_id in run_ids:
                task_group.start_soon(consume_into, run_id)
            await anyio.sleep(0)
            for run_id in run_ids:
                task_group.start_soon(producer, run_id)

    for run_id in run_ids:
        events = results[run_id]
        assert events[-1] is END_SENTINEL, f"Run {run_id} did not receive END sentinel"


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_make_stream_bridge_defaults():
    """make_stream_bridge() with no config yields a MemoryStreamBridge."""
    async with make_stream_bridge() as bridge:
        assert isinstance(bridge, MemoryStreamBridge)


# ---------------------------------------------------------------------------
# Configurable heartbeat interval
# ---------------------------------------------------------------------------


def _app_config_with_heartbeat(value: float | None):
    """Build a minimal AppConfig stand-in exposing ``stream_bridge``."""
    from types import SimpleNamespace

    from deerflow.config.stream_bridge_config import StreamBridgeConfig

    if value is None:
        return SimpleNamespace(stream_bridge=None)
    return SimpleNamespace(stream_bridge=StreamBridgeConfig(heartbeat_interval_seconds=value))


def test_heartbeat_interval_explicit_argument_wins(monkeypatch):
    """An explicit argument overrides the configured value."""
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: _app_config_with_heartbeat(30.0))

    assert memory_module._resolve_heartbeat_interval(7.5) == 7.5


def test_heartbeat_interval_defaults_to_15_without_config(monkeypatch):
    """No ``stream_bridge`` section falls back to the 15s default."""
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: _app_config_with_heartbeat(None))

    assert memory_module._resolve_heartbeat_interval(None) == 15.0


def test_heartbeat_interval_defaults_to_15_when_config_load_fails(monkeypatch):
    """A broken config must never break streaming."""

    def _boom():
        raise RuntimeError("config exploded")

    monkeypatch.setattr("deerflow.config.app_config.get_app_config", _boom)

    assert memory_module._resolve_heartbeat_interval(None) == 15.0


def test_heartbeat_interval_uses_configured_value(monkeypatch):
    """The configured value is used when no explicit argument is given."""
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: _app_config_with_heartbeat(42.0))

    assert memory_module._resolve_heartbeat_interval(None) == 42.0


@pytest.mark.parametrize("bad_value", [0, -1, 4, 61])
def test_stream_bridge_config_rejects_out_of_range_heartbeat(bad_value):
    """Values outside 5-60 seconds are rejected at config load time."""
    from pydantic import ValidationError

    from deerflow.config.stream_bridge_config import StreamBridgeConfig

    with pytest.raises(ValidationError):
        StreamBridgeConfig(heartbeat_interval_seconds=bad_value)


@pytest.mark.parametrize("ok_value", [5, 60])
def test_stream_bridge_config_accepts_heartbeat_range_bounds(ok_value):
    from deerflow.config.stream_bridge_config import StreamBridgeConfig

    assert StreamBridgeConfig(heartbeat_interval_seconds=ok_value).heartbeat_interval_seconds == ok_value


@pytest.mark.anyio
async def test_subscribe_uses_resolved_interval(bridge: MemoryStreamBridge, monkeypatch):
    """subscribe() without an explicit interval must use the resolved value."""
    run_id = "run-resolved"
    bridge._get_or_create_stream(run_id)
    monkeypatch.setattr(memory_module, "_resolve_heartbeat_interval", lambda _: 0.05)

    async def consumer() -> bool:
        async for entry in bridge.subscribe(run_id):
            if entry is HEARTBEAT_SENTINEL:
                return True
        return False  # pragma: no cover

    assert await asyncio.wait_for(consumer(), timeout=2.0) is True
