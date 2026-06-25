from __future__ import annotations

from pathlib import Path
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.app import create_app
from app.gateway.auth.models import User
from app.gateway.routers import files
from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths

_USER_ID = UUID("22222222-2222-4222-8222-222222222222")


def _stable_user() -> User:
    return User(
        id=_USER_ID,
        email="files-test@example.com",
        password_hash="x",
        system_role="user",
    )


def _make_app(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path))
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(files.router)
    return app


def test_user_files_upload_list_download_and_delete(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        created_folder = client.post(
            "/api/files/folders",
            json={"name": "Reports", "parent_path": ""},
        )
        assert created_folder.status_code == 201
        assert created_folder.json()["kind"] == "folder"

        uploaded = client.post(
            "/api/files/upload",
            data={"folder_path": "Reports"},
            files={"files": ("summary.txt", b"hello", "text/plain")},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["total"] == 1

        listed = client.get("/api/files?folder_path=Reports")
        assert listed.status_code == 200
        body = listed.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "summary.txt"
        assert body["items"][0]["source"] == "uploaded"

        downloaded = client.get("/api/files/Reports/summary.txt")
        assert downloaded.status_code == 200
        assert downloaded.content == b"hello"

        deleted = client.delete("/api/files/Reports/summary.txt")
        assert deleted.status_code == 200
        assert deleted.json()["success"] is True


def test_user_files_reject_path_traversal(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        response = client.get("/api/files?folder_path=..")

    assert response.status_code == 400


def test_user_files_list_folders_and_keep_them_in_uploaded_filter(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        assert (
            client.post(
                "/api/files/folders",
                json={"name": "Reports", "parent_path": ""},
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/files/folders",
                json={"name": "2026", "parent_path": "Reports"},
            ).status_code
            == 201
        )

        filtered = client.get("/api/files?source=uploaded&type=folder&q=report")
        assert filtered.status_code == 200
        assert [item["path"] for item in filtered.json()["items"]] == ["Reports"]

        folders = client.get("/api/files/folders")
        assert folders.status_code == 200
        assert folders.json()["folders"] == ["Reports", "Reports/2026"]


def test_user_files_upload_limit_is_described_and_partial_file_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "MAX_UPLOAD_BYTES", 4)
    app = _make_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        config = client.get("/api/files/upload-config")
        assert config.status_code == 200
        assert config.json() == {
            "max_upload_bytes": 4,
            "max_upload_label": "4 bytes",
        }

        uploaded = client.post(
            "/api/files/upload",
            files=[
                ("files", ("small.txt", b"1234", "text/plain")),
                ("files", ("too-large.txt", b"12345", "text/plain")),
            ],
        )
        assert uploaded.status_code == 413
        assert "Maximum size per file is 4 bytes" in uploaded.json()["detail"]

        listed = client.get("/api/files")
        assert listed.status_code == 200
        assert listed.json()["items"] == []


def test_gateway_app_mounts_files_router():
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/files" in paths
    assert "/api/files/upload" in paths
    assert "/api/files/folders" in paths
    assert "/api/files/upload-config" in paths


def test_file_type_recognizes_image_extensions_without_mime():
    assert files._file_type(".png", None) == "image"
    assert files._file_type(".webp", None) == "image"
