from __future__ import annotations

from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.routers import file_publications
from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.persistence.file_publication.model import FilePublicationRow
from deerflow.persistence.user.model import UserRow

OWNER_ID = "00000000-0000-0000-0000-000000000001"
OTHER_ID = "00000000-0000-0000-0000-000000000002"


def _user(user_id: str, email: str) -> User:
    return User(id=UUID(user_id), email=email, password_hash="x", system_role="user")


async def _setup_database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'publications.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(UserRow.__table__.create)
        await connection.run_sync(FilePublicationRow.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                UserRow(id=OWNER_ID, email="owner@example.com", password_hash="x", system_role="user"),
                UserRow(id=OTHER_ID, email="other@example.com", password_hash="x", system_role="user"),
            ]
        )
        await session.commit()
    return engine, session_factory


def _app_for(user: User, *, owner_check_passes: bool = True):
    app = make_authed_test_app(
        user_factory=lambda: user,
        owner_check_passes=owner_check_passes,
    )
    app.include_router(file_publications.router)
    return app


def _public_app():
    app = FastAPI()
    app.include_router(file_publications.router)
    return app


@pytest.mark.anyio
async def test_owner_publishes_generated_html_once_and_lists_it(tmp_path, monkeypatch):
    engine, session_factory = await _setup_database(tmp_path)
    paths = Paths(tmp_path / "data")
    monkeypatch.setattr(file_publications, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(paths_module, "_paths", paths)

    virtual_path = "/mnt/user-data/outputs/report.html"
    target = paths.resolve_virtual_path("thread-1", virtual_path, user_id=OWNER_ID)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<h1>Public report</h1>", encoding="utf-8")

    request_body = {"thread_id": "thread-1", "path": virtual_path}
    owner_app = _app_for(_user(OWNER_ID, "owner@example.com"))
    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url="http://test") as client:
        created = await client.post("/api/file-publications", json=request_body)
        repeated = await client.post("/api/file-publications", json=request_body)
        listed = await client.get("/api/file-publications")

    assert created.status_code == 201
    assert created.json()["public_url"].startswith("/published/")
    assert repeated.status_code == 201
    assert repeated.json()["id"] == created.json()["id"]
    assert repeated.json()["public_token"] == created.json()["public_token"]
    assert listed.status_code == 200
    assert listed.json() == {"items": [created.json()], "total": 1}
    assert owner_app.state.thread_store.check_access.await_count == 2

    other_app = _app_for(_user(OTHER_ID, "other@example.com"))
    async with AsyncClient(transport=ASGITransport(app=other_app), base_url="http://test") as client:
        other_list = await client.get("/api/file-publications")
    assert other_list.json() == {"items": [], "total": 0}

    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("virtual_path", "create_source", "expected_status"),
    [
        ("/mnt/user-data/outputs/report.pdf", True, 400),
        ("/mnt/user-data/uploads/report.html", True, 400),
        ("/mnt/user-data/outputs/missing.html", False, 404),
    ],
)
async def test_publish_rejects_invalid_or_missing_generated_html(
    tmp_path,
    monkeypatch,
    virtual_path,
    create_source,
    expected_status,
):
    engine, session_factory = await _setup_database(tmp_path)
    paths = Paths(tmp_path / "data")
    monkeypatch.setattr(file_publications, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(paths_module, "_paths", paths)

    if create_source:
        target = paths.resolve_virtual_path("thread-1", virtual_path, user_id=OWNER_ID)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("not publishable", encoding="utf-8")

    app = _app_for(_user(OWNER_ID, "owner@example.com"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/file-publications",
            json={"thread_id": "thread-1", "path": virtual_path},
        )
    assert response.status_code == expected_status

    await engine.dispose()


@pytest.mark.anyio
async def test_publish_hides_a_conversation_owned_by_someone_else(tmp_path, monkeypatch):
    engine, session_factory = await _setup_database(tmp_path)
    paths = Paths(tmp_path / "data")
    monkeypatch.setattr(file_publications, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(paths_module, "_paths", paths)

    app = _app_for(
        _user(OTHER_ID, "other@example.com"),
        owner_check_passes=False,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/file-publications",
            json={
                "thread_id": "owner-thread",
                "path": "/mnt/user-data/outputs/report.html",
            },
        )
    assert response.status_code == 404

    await engine.dispose()


@pytest.mark.anyio
async def test_anyone_can_read_published_html_as_non_sniffable_text(tmp_path, monkeypatch):
    engine, session_factory = await _setup_database(tmp_path)
    paths = Paths(tmp_path / "data")
    monkeypatch.setattr(file_publications, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(paths_module, "_paths", paths)

    virtual_path = "/mnt/user-data/outputs/interactive.html"
    html = '<button onclick="this.textContent=\'done\'">run</button>'
    target = paths.resolve_virtual_path("thread-1", virtual_path, user_id=OWNER_ID)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")

    owner_app = _app_for(_user(OWNER_ID, "owner@example.com"))
    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url="http://test") as client:
        created = await client.post(
            "/api/file-publications",
            json={"thread_id": "thread-1", "path": virtual_path},
        )
    token = created.json()["public_token"]

    async with AsyncClient(transport=ASGITransport(app=_public_app()), base_url="http://test") as client:
        metadata = await client.get(f"/api/public-files/{token}")
        content = await client.get(f"/api/public-files/{token}/content")

    assert metadata.status_code == 200
    assert metadata.json() == {
        "name": "interactive.html",
        "content_url": f"/api/public-files/{token}/content",
    }
    assert content.status_code == 200
    assert content.text == html
    assert content.headers["content-type"].startswith("text/plain")
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["cache-control"] == "no-store"

    await engine.dispose()


@pytest.mark.anyio
async def test_only_owner_can_cancel_a_publication_and_link_then_returns_404(tmp_path, monkeypatch):
    engine, session_factory = await _setup_database(tmp_path)
    paths = Paths(tmp_path / "data")
    monkeypatch.setattr(file_publications, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(paths_module, "_paths", paths)

    virtual_path = "/mnt/user-data/outputs/revocable.html"
    target = paths.resolve_virtual_path("thread-1", virtual_path, user_id=OWNER_ID)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<p>published</p>", encoding="utf-8")

    owner_app = _app_for(_user(OWNER_ID, "owner@example.com"))
    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url="http://test") as client:
        created = await client.post(
            "/api/file-publications",
            json={"thread_id": "thread-1", "path": virtual_path},
        )
    publication_id = created.json()["id"]
    token = created.json()["public_token"]

    other_app = _app_for(_user(OTHER_ID, "other@example.com"))
    async with AsyncClient(transport=ASGITransport(app=other_app), base_url="http://test") as client:
        denied = await client.delete(f"/api/file-publications/{publication_id}")
    assert denied.status_code == 404

    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url="http://test") as client:
        cancelled = await client.delete(f"/api/file-publications/{publication_id}")
    assert cancelled.status_code == 204

    async with AsyncClient(transport=ASGITransport(app=_public_app()), base_url="http://test") as client:
        missing = await client.get(f"/api/public-files/{token}")
    assert missing.status_code == 404

    await engine.dispose()


@pytest.mark.anyio
async def test_replacement_file_requires_explicit_republish_but_keeps_public_url(tmp_path, monkeypatch):
    engine, session_factory = await _setup_database(tmp_path)
    paths = Paths(tmp_path / "data")
    monkeypatch.setattr(file_publications, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(paths_module, "_paths", paths)

    virtual_path = "/mnt/user-data/outputs/replaceable.html"
    target = paths.resolve_virtual_path("thread-1", virtual_path, user_id=OWNER_ID)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<p>original</p>", encoding="utf-8")

    request_body = {"thread_id": "thread-1", "path": virtual_path}
    owner_app = _app_for(_user(OWNER_ID, "owner@example.com"))
    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url="http://test") as client:
        created = await client.post("/api/file-publications", json=request_body)
    token = created.json()["public_token"]

    target.unlink()
    target.write_text("<p>replacement</p>", encoding="utf-8")
    async with AsyncClient(transport=ASGITransport(app=_public_app()), base_url="http://test") as client:
        stale = await client.get(f"/api/public-files/{token}/content")
    assert stale.status_code == 404

    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url="http://test") as client:
        republished = await client.post("/api/file-publications", json=request_body)
    assert republished.json()["public_token"] == token

    async with AsyncClient(transport=ASGITransport(app=_public_app()), base_url="http://test") as client:
        refreshed = await client.get(f"/api/public-files/{token}/content")
    assert refreshed.text == "<p>replacement</p>"

    await engine.dispose()
