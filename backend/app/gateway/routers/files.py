"""User-scoped document library routes."""

from __future__ import annotations

import json
import mimetypes
import os
import stat
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.gateway.deps import get_current_user
from deerflow.config.paths import get_paths
from deerflow.uploads.manager import claim_unique_filename, normalize_filename, open_upload_file_no_symlink

router = APIRouter(prefix="/api/files", tags=["files"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
METADATA_FILENAME = ".deerflow-files.json"
ACTIVE_CONTENT_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
}
FileSource = Literal["uploaded", "generated"]
ItemKind = Literal["file", "folder"]


class FileItem(BaseModel):
    id: str
    name: str
    path: str
    kind: ItemKind
    source: FileSource | None = None
    size: int = 0
    mime_type: str | None = None
    extension: str = ""
    modified_at: datetime
    preview_url: str | None = None
    download_url: str | None = None


class FileListResponse(BaseModel):
    folder_path: str = ""
    items: list[FileItem]
    total: int


class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    parent_path: str = ""


class DeleteResponse(BaseModel):
    success: bool
    message: str


async def _require_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None) is not None:
        return str(user.id)
    user_id = await get_current_user(request)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return str(user_id)


def _library_dir(user_id: str) -> Path:
    root = get_paths().user_documents_dir(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _metadata_path(root: Path) -> Path:
    return root / METADATA_FILENAME


def _load_metadata(root: Path) -> dict[str, dict[str, str]]:
    path = _metadata_path(root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_metadata(root: Path, metadata: dict[str, dict[str, str]]) -> None:
    path = _metadata_path(root)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_library_path(value: str | None) -> str:
    raw = (value or "").strip().replace("\\", "/").strip("/")
    if not raw:
        return ""
    parts = PurePosixPath(raw).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=400, detail="Invalid path")
    return "/".join(parts)


def _resolve_inside(root: Path, value: str | None) -> Path:
    relative = _normalize_library_path(value)
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path") from None
    return target


def _relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _file_type(extension: str, mime_type: str | None) -> str:
    ext = extension.lower()
    if mime_type and mime_type.startswith("image/"):
        return "image"
    if mime_type and mime_type.startswith("audio/"):
        return "audio"
    if ext in {".pdf", ".doc", ".docx", ".md", ".txt", ".csv", ".xls", ".xlsx", ".ppt", ".pptx"}:
        return "document"
    return "other"


def _item_from_path(root: Path, path: Path, metadata: dict[str, dict[str, str]]) -> FileItem:
    stat_result = path.stat()
    relative = _relative_path(root, path)
    modified_at = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC)
    if path.is_dir():
        return FileItem(
            id=relative,
            name=path.name,
            path=relative,
            kind="folder",
            modified_at=modified_at,
        )

    mime_type, _ = mimetypes.guess_type(path.name)
    source = metadata.get(relative, {}).get("source") or "uploaded"
    preview_url = f"/api/files/{quote(relative, safe='/')}" if mime_type and mime_type.startswith("image/") else None
    return FileItem(
        id=relative,
        name=path.name,
        path=relative,
        kind="file",
        source="generated" if source == "generated" else "uploaded",
        size=stat_result.st_size,
        mime_type=mime_type,
        extension=path.suffix.lower(),
        modified_at=modified_at,
        preview_url=preview_url,
        download_url=f"/api/files/{quote(relative, safe='')}?download=true",
    )


@router.get("", response_model=FileListResponse)
async def list_files(
    request: Request,
    folder_path: str = "",
    source: str = "all",
    type: str = "all",  # noqa: A002 - public query parameter
    q: str = "",
) -> FileListResponse:
    root = _library_dir(await _require_user_id(request))
    folder = _resolve_inside(root, folder_path)
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a folder")

    metadata = _load_metadata(root)
    query = q.strip().lower()
    items: list[FileItem] = []
    for entry in folder.iterdir():
        if entry.name == METADATA_FILENAME:
            continue
        if entry.is_symlink():
            continue
        item = _item_from_path(root, entry, metadata)
        if query and query not in item.name.lower():
            continue
        if source != "all" and item.source != source:
            continue
        if type != "all":
            if type == "folder" and item.kind != "folder":
                continue
            if type != "folder" and item.kind == "file" and _file_type(item.extension, item.mime_type) != type:
                continue
            if type != "folder" and item.kind == "folder":
                continue
        items.append(item)

    items.sort(key=lambda item: (item.kind != "folder", item.name.lower()))
    normalized_folder = _normalize_library_path(folder_path)
    return FileListResponse(folder_path=normalized_folder, items=items, total=len(items))


@router.post("/folders", response_model=FileItem, status_code=status.HTTP_201_CREATED)
async def create_folder(data: FolderCreateRequest, request: Request) -> FileItem:
    root = _library_dir(await _require_user_id(request))
    parent = _resolve_inside(root, data.parent_path)
    if not parent.exists():
        raise HTTPException(status_code=404, detail="Parent folder not found")
    if not parent.is_dir():
        raise HTTPException(status_code=400, detail="Parent path is not a folder")

    name = normalize_filename(data.name.strip())
    folder = _resolve_inside(root, f"{_normalize_library_path(data.parent_path)}/{name}".strip("/"))
    if folder.exists():
        raise HTTPException(status_code=409, detail="Folder already exists")
    folder.mkdir(parents=False)
    return _item_from_path(root, folder, _load_metadata(root))


@router.post("/upload", response_model=FileListResponse, status_code=status.HTTP_201_CREATED)
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    folder_path: str = Form(""),
) -> FileListResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    root = _library_dir(await _require_user_id(request))
    folder = _resolve_inside(root, folder_path)
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a folder")

    metadata = _load_metadata(root)
    seen = {entry.name for entry in folder.iterdir() if not entry.is_symlink()}
    uploaded: list[FileItem] = []
    for upload in files:
        if not upload.filename:
            continue
        safe_name = claim_unique_filename(normalize_filename(upload.filename), seen)
        dest, fh = open_upload_file_no_symlink(folder, safe_name)
        size = 0
        try:
            while chunk := await upload.read(8192):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"File too large: {safe_name}")
                fh.write(chunk)
        finally:
            fh.close()
        os.chmod(dest, stat.S_IMODE(os.stat(dest).st_mode) | stat.S_IRGRP | stat.S_IROTH)
        metadata[_relative_path(root, dest)] = {
            "source": "uploaded",
            "uploaded_at": datetime.now(UTC).isoformat(),
        }
        uploaded.append(_item_from_path(root, dest, metadata))

    _save_metadata(root, metadata)
    return FileListResponse(folder_path=_normalize_library_path(folder_path), items=uploaded, total=len(uploaded))


@router.get("/{path:path}")
async def get_file(path: str, request: Request, download: bool = False) -> FileResponse:
    root = _library_dir(await _require_user_id(request))
    target = _resolve_inside(root, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    mime_type, _ = mimetypes.guess_type(target.name)
    headers = {}
    if download or mime_type in ACTIVE_CONTENT_MIME_TYPES:
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(target.name)}"
    return FileResponse(target, media_type=mime_type, filename=target.name if download else None, headers=headers)


@router.delete("/{path:path}", response_model=DeleteResponse)
async def delete_file(path: str, request: Request) -> DeleteResponse:
    root = _library_dir(await _require_user_id(request))
    target = _resolve_inside(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    metadata = _load_metadata(root)
    relative = _relative_path(root, target)
    if target.is_dir():
        try:
            target.rmdir()
        except OSError:
            raise HTTPException(status_code=409, detail="Folder is not empty") from None
    elif target.is_file():
        target.unlink()
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    metadata.pop(relative, None)
    _save_metadata(root, metadata)
    return DeleteResponse(success=True, message=f"Deleted {relative}")
