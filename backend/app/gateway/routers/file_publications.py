"""Permanent public links for conversation-generated HTML files."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.gateway.deps import get_thread_store
from deerflow.config.paths import get_paths
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.file_publication.model import FilePublicationRow

router = APIRouter(tags=["file-publications"])


class CreateFilePublicationRequest(BaseModel):
    """Publish one generated HTML artifact owned by the current user."""

    thread_id: str = Field(..., min_length=1, max_length=64)
    path: str = Field(..., min_length=1, max_length=2048)


class FilePublicationResponse(BaseModel):
    id: str
    name: str
    thread_id: str
    path: str
    public_token: str
    public_url: str
    created_at: datetime


class FilePublicationListResponse(BaseModel):
    items: list[FilePublicationResponse]
    total: int


def _request_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None or getattr(user, "id", None) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return str(user.id)


def _normalize_generated_html_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    prefix = "/mnt/user-data/outputs/"
    if not normalized.startswith(prefix):
        raise HTTPException(status_code=400, detail="Published files must be generated outputs")
    if Path(normalized).suffix.lower() not in {".html", ".htm"}:
        raise HTTPException(status_code=400, detail="Only generated HTML files can be published")
    return normalized


def _file_identity(target: Path) -> str:
    stat_result = target.stat()
    return f"{stat_result.st_dev}:{stat_result.st_ino}"


async def _resolve_owned_html_source(
    request: Request,
    owner_user_id: str,
    thread_id: str,
    source_path: str,
) -> tuple[str, Path]:
    allowed = await get_thread_store(request).check_access(
        thread_id,
        owner_user_id,
        require_existing=True,
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="Conversation not found")

    normalized = _normalize_generated_html_path(source_path)
    try:
        target = get_paths().resolve_virtual_path(
            thread_id,
            normalized,
            user_id=owner_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Generated HTML file not found")
    return normalized, target


def _publication_response(row: FilePublicationRow, target: Path) -> FilePublicationResponse:
    return FilePublicationResponse(
        id=row.id,
        name=target.name,
        thread_id=row.thread_id,
        path=row.source_path,
        public_token=row.public_token,
        public_url=f"/published/{row.public_token}",
        created_at=row.created_at,
    )


def _resolve_publication_source(row: FilePublicationRow) -> Path:
    try:
        target = get_paths().resolve_virtual_path(
            row.thread_id,
            row.source_path,
            user_id=row.owner_user_id,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Published file not found") from None
    if not target.is_file() or _file_identity(target) != row.source_identity:
        raise HTTPException(status_code=404, detail="Published file not found")
    return target


@router.post(
    "/api/file-publications",
    response_model=FilePublicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a generated HTML file",
)
async def create_file_publication(
    body: CreateFilePublicationRequest,
    request: Request,
) -> FilePublicationResponse:
    owner_user_id = _request_user_id(request)
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database not available")

    source_path, target = await _resolve_owned_html_source(
        request,
        owner_user_id,
        body.thread_id,
        body.path,
    )
    source_identity = _file_identity(target)

    async with sf() as session:
        existing = (
            await session.execute(
                select(FilePublicationRow).where(
                    FilePublicationRow.owner_user_id == owner_user_id,
                    FilePublicationRow.thread_id == body.thread_id,
                    FilePublicationRow.source_path == source_path,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.source_identity != source_identity:
                existing.source_identity = source_identity
                await session.commit()
                await session.refresh(existing)
            return _publication_response(existing, target)

        row = FilePublicationRow(
            id=str(uuid.uuid4()),
            public_token=secrets.token_urlsafe(32),
            owner_user_id=owner_user_id,
            thread_id=body.thread_id,
            source_path=source_path,
            source_identity=source_identity,
            created_at=datetime.now(UTC),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _publication_response(row, target)


@router.get(
    "/api/file-publications",
    response_model=FilePublicationListResponse,
    summary="List the current user's public HTML links",
)
async def list_file_publications(request: Request) -> FilePublicationListResponse:
    owner_user_id = _request_user_id(request)
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with sf() as session:
        rows = (
            await session.execute(
                select(FilePublicationRow)
                .where(FilePublicationRow.owner_user_id == owner_user_id)
                .order_by(FilePublicationRow.created_at.desc())
            )
        ).scalars()
        publications = list(rows)

    items: list[FilePublicationResponse] = []
    for row in publications:
        try:
            target = _resolve_publication_source(row)
        except HTTPException:
            continue
        items.append(_publication_response(row, target))
    return FilePublicationListResponse(items=items, total=len(items))
