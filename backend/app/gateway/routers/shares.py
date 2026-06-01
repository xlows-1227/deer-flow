"""Thread share endpoints — create and read shared conversation links."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.authz import require_permission
from app.gateway.deps import get_checkpointer
from app.gateway.utils import sanitize_log_param
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.thread_share.model import ThreadShareRow
from deerflow.runtime import serialize_channel_values
from deerflow.utils.time import coerce_iso

logger = logging.getLogger(__name__)
router = APIRouter(tags=["shares"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateShareRequest(BaseModel):
    """Request body for creating a thread share."""

    expires_in_days: int | None = Field(
        default=30,
        ge=1,
        le=365,
        description="Number of days until the share link expires. Null means never expires.",
    )


class CreateShareResponse(BaseModel):
    """Response model for creating a thread share."""

    share_token: str
    share_url: str
    expires_at: str | None


class SharedMessage(BaseModel):
    """A single message in a shared thread."""

    type: str
    id: str | None
    content: str
    created_at: str | None = None


class SharedThreadResponse(BaseModel):
    """Response model for reading a shared thread."""

    thread_id: str
    title: str | None
    created_at: str | None
    messages: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_messages(raw_messages: list[Any]) -> list[dict[str, Any]]:
    """Normalize messages for public sharing."""
    result: list[dict[str, Any]] = []
    for msg in raw_messages:
        if not isinstance(msg, dict):
            continue
        msg_type = msg.get("type") or msg.get("_type")
        if msg_type not in ("human", "ai", "tool"):
            continue
        # Skip hidden/internal messages
        name = msg.get("name") or msg.get("kwargs", {}).get("name")
        if name in ("summary", "loop_warning", "todo_reminder", "todo_completion_reminder"):
            continue
        # Extract content
        content = ""
        raw_content = msg.get("content")
        if isinstance(raw_content, str):
            content = raw_content
        elif isinstance(raw_content, list):
            # Content blocks (e.g. OpenAI format)
            texts = []
            for block in raw_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
            content = "\n".join(texts)

        result.append({
            "type": msg_type,
            "id": msg.get("id"),
            "content": content,
            "name": name,
        })
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/api/threads/{thread_id}/share",
    response_model=CreateShareResponse,
    summary="Create a shareable link for a thread",
)
@require_permission("threads", "read", owner_check=True, require_existing=True)
async def create_thread_share(
    thread_id: str,
    body: CreateShareRequest,
    request: Request,
) -> CreateShareResponse:
    """Generate a public share token for the given thread."""
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database not available")

    share_token = str(uuid.uuid4()).replace("-", "")
    expires_at = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=body.expires_in_days)

    row = ThreadShareRow(
        share_token=share_token,
        thread_id=thread_id,
        expires_at=expires_at,
    )
    async with sf() as session:
        session.add(row)
        await session.commit()

    logger.info(
        "Created share link for thread %s: token=%s",
        sanitize_log_param(thread_id),
        share_token,
    )

    return CreateShareResponse(
        share_token=share_token,
        share_url="",
        expires_at=expires_at.isoformat() if expires_at else None,
    )


@router.get(
    "/api/share/{token}",
    response_model=SharedThreadResponse,
    summary="Get shared thread content (read-only, no auth required)",
)
async def get_shared_thread(token: str, request: Request) -> SharedThreadResponse:
    """Return the conversation content for a public share token."""
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with sf() as session:
        row = await session.get(ThreadShareRow, token)

    if row is None:
        raise HTTPException(status_code=404, detail="Share link not found or expired")

    expires_at = row.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            raise HTTPException(status_code=410, detail="Share link has expired")

    thread_id = row.thread_id

    # Read thread metadata from checkpointer
    checkpointer = get_checkpointer(request)
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    try:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
    except Exception:
        logger.exception("Failed to get checkpoint for shared thread %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to load shared thread")

    if checkpoint_tuple is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    metadata = getattr(checkpoint_tuple, "metadata", {}) or {}
    channel_values = checkpoint.get("channel_values", {})

    title = channel_values.get("title")
    raw_messages = channel_values.get("messages", [])
    messages = serialize_channel_values({"messages": raw_messages}).get("messages", [])

    return SharedThreadResponse(
        thread_id=thread_id,
        title=title,
        created_at=coerce_iso(metadata.get("created_at", "")),
        messages=_format_messages(messages),
    )
