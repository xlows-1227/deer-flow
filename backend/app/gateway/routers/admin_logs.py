"""Admin log-viewer endpoint: return the tail of gateway.log / frontend.log.

Both logs live under ``<project_root>/logs/``:
- ``gateway.log``  — backend (FastAPI / Uvicorn) log
- ``frontend.log`` — Next.js frontend log

Only the trailing ``MAX_TAIL_BYTES`` are returned so the response stays
bounded even when the log file has grown large. Requires an admin session.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.gateway.auth.models import User
from app.gateway.deps import get_current_user_from_request
from deerflow.config.runtime_paths import project_root

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/logs", tags=["admin-logs"])

# Cap the returned payload so a multi-MB log doesn't blow up the response
# or the browser. 256 KiB is enough to inspect recent activity.
MAX_TAIL_BYTES = 256 * 1024

_LOG_FILES = {
    "backend": "gateway.log",
    "frontend": "frontend.log",
}


class LogViewResponse(BaseModel):
    source: str = Field(..., description="Log source identifier: 'backend' | 'frontend'")
    path: str = Field(..., description="Absolute path of the log file on the server")
    truncated: bool = Field(
        ..., description="True if the file was larger than MAX_TAIL_BYTES and only the tail is returned"
    )
    size: int = Field(..., description="Total size of the log file in bytes")
    content: str = Field(..., description="Tail of the log file content")


async def _require_admin_user(
    user: User = Depends(get_current_user_from_request),
) -> User:
    if user.system_role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


@router.get(
    "",
    response_model=LogViewResponse,
    summary="View Server Log (admin)",
    description=(
        "Return the tail of a server log file. `source=backend` returns "
        "logs/gateway.log (FastAPI / Uvicorn); `source=frontend` returns "
        "logs/frontend.log (Next.js). Only the last 256 KiB is returned "
        "when the file is larger. Admin only."
    ),
)
async def get_log(
    source: str = "backend",
    admin: User = Depends(_require_admin_user),
) -> LogViewResponse:
    normalized = source.strip().lower()
    if normalized not in _LOG_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid log source '{source}'. Expected one of: {', '.join(sorted(_LOG_FILES))}",
        )
    log_file_name = _LOG_FILES[normalized]
    log_path = project_root() / "logs" / log_file_name
    if not log_path.exists():
        # File not found is not a 500 — return an empty payload so the UI
        # can render "no logs yet" instead of an error.
        return LogViewResponse(
            source=normalized,
            path=str(log_path),
            truncated=False,
            size=0,
            content="",
        )
    try:
        size = log_path.stat().st_size
        truncated = size > MAX_TAIL_BYTES
        with log_path.open("rb") as f:
            if truncated:
                f.seek(-MAX_TAIL_BYTES, 2)
                raw = f.read(MAX_TAIL_BYTES)
                # Drop the partial first line so the payload starts on a
                # clean line boundary.
                first_nl = raw.find(b"\n")
                if first_nl >= 0:
                    raw = raw[first_nl + 1 :]
            else:
                raw = f.read()
        content = raw.decode("utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Failed to read log file %s: %s", log_path, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read log: {exc}") from exc
    return LogViewResponse(
        source=normalized,
        path=str(log_path),
        truncated=truncated,
        size=size,
        content=content,
    )
