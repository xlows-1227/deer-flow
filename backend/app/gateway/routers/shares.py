"""Read-only thread-link and registered-user file sharing endpoints."""

from __future__ import annotations

import logging
import mimetypes
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.gateway.authz import require_permission
from app.gateway.deps import get_checkpointer, get_thread_store
from app.gateway.skill_redaction import redact_channel_values
from app.gateway.utils import sanitize_log_param
from deerflow.config.paths import get_paths
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.file_share.model import FileShareRow
from deerflow.persistence.thread_share.model import ThreadShareRow
from deerflow.persistence.user.model import UserRow
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


FileShareSourceType = Literal[
    "library",
    "conversation_upload",
    "conversation_generated",
]


class CreateFileShareRequest(BaseModel):
    """Share one owned file with another registered user."""

    recipient_email: EmailStr
    source_type: FileShareSourceType
    path: str = Field(..., min_length=1, max_length=2048)
    thread_id: str | None = Field(default=None, max_length=64)


class SharedFileResponse(BaseModel):
    """Read-only file metadata exposed to a share recipient."""

    id: str
    name: str
    size: int
    mime_type: str | None
    extension: str
    modified_at: datetime
    shared_at: datetime
    owner_email: str
    source_type: FileShareSourceType
    preview_url: str
    download_url: str


class SharedFileListResponse(BaseModel):
    items: list[SharedFileResponse]
    total: int


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

        result.append(
            {
                "type": msg_type,
                "id": msg.get("id"),
                "content": content,
                "name": name,
            }
        )
    return result


def _request_user(request: Request) -> Any:
    user = getattr(request.state, "user", None)
    if user is None or getattr(user, "id", None) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def _normalize_library_share_path(value: str) -> str:
    raw = value.strip().replace("\\", "/").strip("/")
    parts = PurePosixPath(raw).parts
    if not raw or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=400, detail="Invalid file path")
    return "/".join(parts)


def _normalize_conversation_share_path(source_type: FileShareSourceType, value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if source_type == "conversation_upload":
        prefix = "/mnt/user-data/uploads/"
        filename = raw[len(prefix) :] if raw.startswith(prefix) else raw.strip("/")
        if not filename or "/" in filename or filename in {".", ".."}:
            raise HTTPException(status_code=400, detail="Invalid uploaded file path")
        return f"{prefix}{filename}"

    prefix = "/mnt/user-data/outputs/"
    if not raw.startswith(prefix):
        raise HTTPException(status_code=400, detail="Generated files must be under /mnt/user-data/outputs")
    return raw


def _file_identity(target: Path) -> str:
    """Return a stable identity for the currently shared filesystem object."""

    stat_result = target.stat()
    return f"{stat_result.st_dev}:{stat_result.st_ino}"


async def _resolve_owned_share_source(
    request: Request,
    owner_user_id: str,
    source_type: FileShareSourceType,
    source_path: str,
    thread_id: str | None,
) -> tuple[str, str, Path]:
    if source_type == "library":
        if thread_id:
            raise HTTPException(status_code=400, detail="Library files must not include a thread id")
        normalized = _normalize_library_share_path(source_path)
        root = get_paths().user_documents_dir(owner_user_id).resolve()
        candidate = root / normalized
        if candidate.is_symlink():
            raise HTTPException(status_code=404, detail="File not found")
        target = candidate.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid file path") from None
        if not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return normalized, "", target

    if not thread_id:
        raise HTTPException(status_code=400, detail="Conversation files require a thread id")
    allowed = await get_thread_store(request).check_access(
        thread_id,
        owner_user_id,
        require_existing=True,
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="Conversation not found")

    normalized = _normalize_conversation_share_path(source_type, source_path)
    try:
        target = get_paths().resolve_virtual_path(thread_id, normalized, user_id=owner_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return normalized, thread_id, target


def _resolve_shared_file(row: FileShareRow) -> Path:
    if row.source_type == "library":
        root = get_paths().user_documents_dir(row.owner_user_id).resolve()
        target = (root / row.source_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=404, detail="Shared file not found") from None
    else:
        try:
            target = get_paths().resolve_virtual_path(
                row.thread_id,
                row.source_path,
                user_id=row.owner_user_id,
            )
        except ValueError:
            raise HTTPException(status_code=404, detail="Shared file not found") from None
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Shared file not found")
    if row.source_identity != _file_identity(target):
        raise HTTPException(status_code=404, detail="Shared file not found")
    return target


def _shared_file_response(row: FileShareRow, target: Path, owner_email: str) -> SharedFileResponse:
    stat_result = target.stat()
    mime_type, _ = mimetypes.guess_type(target.name)
    content_url = f"/api/file-shares/{row.id}/content"
    return SharedFileResponse(
        id=row.id,
        name=target.name,
        size=stat_result.st_size,
        mime_type=mime_type,
        extension=target.suffix.lower(),
        modified_at=datetime.fromtimestamp(stat_result.st_mtime, tz=UTC),
        shared_at=row.created_at,
        owner_email=owner_email,
        source_type=row.source_type,  # type: ignore[arg-type]
        preview_url=content_url,
        download_url=f"{content_url}?download=true",
    )


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


@router.post(
    "/api/file-shares",
    response_model=SharedFileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Share an owned file with a registered user",
)
async def create_file_share(
    body: CreateFileShareRequest,
    request: Request,
) -> SharedFileResponse:
    user = _request_user(request)
    owner_user_id = str(user.id)
    owner_email = str(user.email)
    recipient_email = str(body.recipient_email).strip().lower()
    if recipient_email == owner_email.strip().lower():
        raise HTTPException(status_code=400, detail="You cannot share a file with yourself")

    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database not available")

    source_path, thread_id, target = await _resolve_owned_share_source(
        request,
        owner_user_id,
        body.source_type,
        body.path,
        body.thread_id,
    )
    source_identity = _file_identity(target)

    async with sf() as session:
        recipient = (await session.execute(select(UserRow).where(func.lower(UserRow.email) == recipient_email))).scalar_one_or_none()
        if recipient is None:
            raise HTTPException(status_code=404, detail="No registered user uses that email address")

        existing = (
            await session.execute(
                select(FileShareRow).where(
                    FileShareRow.owner_user_id == owner_user_id,
                    FileShareRow.recipient_user_id == recipient.id,
                    FileShareRow.source_type == body.source_type,
                    FileShareRow.source_path == source_path,
                    FileShareRow.thread_id == thread_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.source_identity != source_identity:
                # The original object was removed and a different one now
                # occupies the same path. An explicit new share request is
                # required before the recipient can access that replacement.
                existing.source_identity = source_identity
                existing.created_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(existing)
            return _shared_file_response(existing, target, owner_email)

        row = FileShareRow(
            id=str(uuid.uuid4()),
            owner_user_id=owner_user_id,
            recipient_user_id=recipient.id,
            source_type=body.source_type,
            source_path=source_path,
            source_identity=source_identity,
            thread_id=thread_id,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    return _shared_file_response(row, target, owner_email)


@router.get(
    "/api/file-shares",
    response_model=SharedFileListResponse,
    summary="List files shared with the current user",
)
async def list_received_file_shares(request: Request) -> SharedFileListResponse:
    recipient_user_id = str(_request_user(request).id)
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with sf() as session:
        records = (await session.execute(select(FileShareRow, UserRow.email).join(UserRow, UserRow.id == FileShareRow.owner_user_id).where(FileShareRow.recipient_user_id == recipient_user_id).order_by(FileShareRow.created_at.desc()))).all()

    items: list[SharedFileResponse] = []
    for row, owner_email in records:
        try:
            target = _resolve_shared_file(row)
        except HTTPException:
            # A share is a live reference. Files removed by their owner no
            # longer appear in the recipient's read-only collection.
            continue
        items.append(_shared_file_response(row, target, owner_email))
    return SharedFileListResponse(items=items, total=len(items))


@router.get(
    "/api/file-shares/{share_id}/content",
    summary="Read or download a file shared with the current user",
)
async def get_shared_file_content(
    share_id: str,
    request: Request,
    download: bool = False,
) -> FileResponse:
    recipient_user_id = str(_request_user(request).id)
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with sf() as session:
        row = (
            await session.execute(
                select(FileShareRow).where(
                    FileShareRow.id == share_id,
                    FileShareRow.recipient_user_id == recipient_user_id,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Shared file not found")

    target = _resolve_shared_file(row)
    mime_type, _ = mimetypes.guess_type(target.name)
    headers = {"X-Content-Type-Options": "nosniff"}
    active_content_types = {"text/html", "application/xhtml+xml", "image/svg+xml"}
    response_mime_type = "text/plain; charset=utf-8" if mime_type in active_content_types and not download else mime_type
    if download:
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(target.name)}"
    return FileResponse(
        target,
        media_type=response_mime_type,
        filename=target.name if download else None,
        headers=headers,
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
    messages = redact_channel_values(
        {"messages": raw_messages},
        boundary_id=thread_id,
    ).get("messages", [])

    return SharedThreadResponse(
        thread_id=thread_id,
        title=title,
        created_at=coerce_iso(metadata.get("created_at", "")),
        messages=_format_messages(messages),
    )
