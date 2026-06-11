"""Mapping from internal Run states to stable External API states."""

from typing import Literal

ExternalRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]

_STATUS_MAP: dict[str, ExternalRunStatus] = {
    "pending": "pending",
    "running": "running",
    "success": "completed",
    "interrupted": "cancelled",
    "error": "failed",
    "timeout": "failed",
}


def to_external_run_status(status: str) -> ExternalRunStatus:
    """Map an internal status to the stable External API contract."""
    return _STATUS_MAP.get(status, "failed")
