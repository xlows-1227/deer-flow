"""Explicit public serializers for published-Agent API responses."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

_FORBIDDEN_KEY_PARTS = (
    "owner_user",
    "release_id",
    "model_name",
    "instructions",
    "secret",
    "hash",
    "connector",
    "skill_revision",
    "credential_id",
    "thread_id",
    "configurable",
)


def assert_public_payload_safe(value: Any, *, path: str = "$") -> None:
    """Recursively fail if an internal authority field reaches a response."""
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key).lower()
            if any(part in key for part in _FORBIDDEN_KEY_PARTS):
                raise ValueError(f"unsafe public response field at {path}.{raw_key}")
            assert_public_payload_safe(item, path=f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_public_payload_safe(item, path=f"{path}[{index}]")


def _finish(payload: dict[str, Any]) -> dict[str, Any]:
    assert_public_payload_safe(payload)
    return payload


def serialize_agent_metadata(agent: dict[str, Any]) -> dict[str, Any]:
    """Serialize the public metadata allowlist for a published Agent."""
    return _finish(
        {
            "agent_id": str(agent["id"]),
            "display_name": str(agent["display_name"]),
            "description": agent.get("description"),
            "avatar": agent.get("avatar_ref"),
        }
    )


def serialize_agent_conversation(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize the public state of a credential-scoped conversation."""
    return _finish(
        {
            "conversation_id": str(row["conversation_id"]),
            "status": str(row["status"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _status(value: Any) -> str:
    raw = value.value if isinstance(value, Enum) else str(value or "error")
    return {
        "success": "completed",
        "error": "failed",
        "interrupted": "cancelled",
    }.get(raw, raw)


def serialize_agent_run(row: Any, *, conversation_id: str) -> dict[str, Any]:
    """Serialize one run without leaking internal runtime authority."""
    getter = (lambda key, default=None: getattr(row, key, default)) if not isinstance(row, dict) else row.get
    status = _status(getter("status"))
    payload = {
        "run_id": str(getter("run_id")),
        "conversation_id": conversation_id,
        "status": status,
        "answer": getter("last_ai_message") if status == "completed" else None,
        "error": "The run failed." if status == "failed" else None,
        "created_at": getter("created_at"),
        "updated_at": getter("updated_at"),
    }
    return _finish(payload)


def sanitize_stream_payload(value: Any) -> Any:
    """Keep only public message content/status fields in SSE payloads."""
    if value is None or isinstance(value, (str, int, float, bool, datetime, date)):
        return value.isoformat() if isinstance(value, (datetime, date)) else value
    if isinstance(value, (list, tuple)):
        return [sanitize_stream_payload(item) for item in value]
    if not isinstance(value, dict):
        return str(value)
    allowed = {
        "id",
        "run_id",
        "conversation_id",
        "type",
        "role",
        "content",
        "status",
        "error",
        "messages",
        "delta",
    }
    sanitized = {str(key): sanitize_stream_payload(item) for key, item in value.items() if str(key) in allowed}
    assert_public_payload_safe(sanitized)
    return sanitized


__all__ = [
    "assert_public_payload_safe",
    "sanitize_stream_payload",
    "serialize_agent_conversation",
    "serialize_agent_metadata",
    "serialize_agent_run",
]
