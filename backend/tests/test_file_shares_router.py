from __future__ import annotations

from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.routers import shares
from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.persistence.file_share.model import FileShareRow
from deerflow.persistence.user.model import UserRow

OWNER_ID = "00000000-0000-0000-0000-000000000001"
RECIPIENT_ID = "00000000-0000-0000-0000-000000000002"
OTHER_ID = "00000000-0000-0000-0000-000000000003"


def _user(user_id: str, email: str) -> User:
    return User(id=UUID(user_id), email=email, password_hash="x", system_role="user")


async def _setup_database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shares.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(UserRow.__table__.create)
        await connection.run_sync(FileShareRow.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                UserRow(id=OWNER_ID, email="owner@example.com", password_hash="x", system_role="user"),
                UserRow(id=RECIPIENT_ID, email="recipient@example.com", password_hash="x", system_role="user"),
                UserRow(id=OTHER_ID, email="other@example.com", password_hash="x", system_role="user"),
            ]
        )
        await session.commit()
    return engine, session_factory


def _app_for(user: User):
    app = make_authed_test_app(user_factory=lambda: user)
    app.include_router(shares.router)
    return app


@pytest.mark.anyio
async def test_registered_user_receives_read_only_markdown_and_html_shares(tmp_path, monkeypatch):
    engine, session_factory = await _setup_database(tmp_path)
    paths = Paths(tmp_path / "data")
    monkeypatch.setattr(shares, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(paths_module, "_paths", paths)

    owner_library = paths.user_documents_dir(OWNER_ID)
    owner_library.mkdir(parents=True)
    (owner_library / "notes.md").write_text("# Shared notes", encoding="utf-8")
    (owner_library / "page.html").write_text("<h1>Shared page</h1><script>alert(1)</script>", encoding="utf-8")

    owner_app = _app_for(_user(OWNER_ID, "owner@example.com"))
    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url="http://test") as client:
        for path in ("notes.md", "page.html"):
            response = await client.post(
                "/api/file-shares",
                json={
                    "recipient_email": "RECIPIENT@example.com",
                    "source_type": "library",
                    "path": path,
                },
            )
            assert response.status_code == 201

        missing_user = await client.post(
            "/api/file-shares",
            json={
                "recipient_email": "missing@example.com",
                "source_type": "library",
                "path": "notes.md",
            },
        )
        assert missing_user.status_code == 404

    recipient_app = _app_for(_user(RECIPIENT_ID, "recipient@example.com"))
    async with AsyncClient(transport=ASGITransport(app=recipient_app), base_url="http://test") as client:
        response = await client.get("/api/file-shares")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert {item["name"] for item in payload["items"]} == {"notes.md", "page.html"}
        assert {item["owner_email"] for item in payload["items"]} == {"owner@example.com"}

        by_name = {item["name"]: item for item in payload["items"]}
        markdown = await client.get(by_name["notes.md"]["preview_url"])
        assert markdown.status_code == 200
        assert markdown.text == "# Shared notes"

        html = await client.get(by_name["page.html"]["preview_url"])
        assert html.status_code == 200
        assert html.headers["content-type"].startswith("text/plain")
        assert "<script>alert(1)</script>" in html.text
        assert html.headers["x-content-type-options"] == "nosniff"

        download = await client.get(by_name["page.html"]["download_url"])
        assert download.headers["content-disposition"].startswith("attachment;")

    # A deleted source must not let its old recipient inherit access to a
    # replacement file created at the same path.
    (owner_library / "notes.md").unlink()
    (owner_library / "notes.md").write_text("replacement", encoding="utf-8")
    async with AsyncClient(transport=ASGITransport(app=recipient_app), base_url="http://test") as client:
        assert (await client.get(by_name["notes.md"]["preview_url"])).status_code == 404
        assert {item["name"] for item in (await client.get("/api/file-shares")).json()["items"]} == {"page.html"}

    # The owner must explicitly share the replacement before it is visible.
    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url="http://test") as client:
        response = await client.post(
            "/api/file-shares",
            json={
                "recipient_email": "recipient@example.com",
                "source_type": "library",
                "path": "notes.md",
            },
        )
        assert response.status_code == 201
    async with AsyncClient(transport=ASGITransport(app=recipient_app), base_url="http://test") as client:
        assert (await client.get(by_name["notes.md"]["preview_url"])).text == "replacement"

    other_app = _app_for(_user(OTHER_ID, "other@example.com"))
    async with AsyncClient(transport=ASGITransport(app=other_app), base_url="http://test") as client:
        response = await client.get(by_name["notes.md"]["preview_url"])
        assert response.status_code == 404

    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("source_type", "virtual_path"),
    [
        ("conversation_upload", "/mnt/user-data/uploads/input.txt"),
        ("conversation_generated", "/mnt/user-data/outputs/report.md"),
    ],
)
async def test_conversation_file_share_checks_owner_and_normalizes_source(
    tmp_path,
    monkeypatch,
    source_type,
    virtual_path,
):
    engine, session_factory = await _setup_database(tmp_path)
    paths = Paths(tmp_path / "data")
    monkeypatch.setattr(shares, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(paths_module, "_paths", paths)

    target = paths.resolve_virtual_path("thread-1", virtual_path, user_id=OWNER_ID)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("shared", encoding="utf-8")

    owner_app = _app_for(_user(OWNER_ID, "owner@example.com"))
    submitted_path = "input.txt" if source_type == "conversation_upload" else virtual_path
    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url="http://test") as client:
        response = await client.post(
            "/api/file-shares",
            json={
                "recipient_email": "recipient@example.com",
                "source_type": source_type,
                "path": submitted_path,
                "thread_id": "thread-1",
            },
        )
    assert response.status_code == 201
    owner_app.state.thread_store.check_access.assert_awaited_once_with(
        "thread-1",
        OWNER_ID,
        require_existing=True,
    )

    await engine.dispose()
