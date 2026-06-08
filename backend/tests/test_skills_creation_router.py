import zipfile
from types import SimpleNamespace
from uuid import uuid4

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

import app.gateway.routers.skills as skills_router
from app.gateway.auth.models import User
from deerflow.skills.security_scanner import ScanResult
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage


def _make_client(
    tmp_path,
    monkeypatch,
    *,
    system_role: str = "user",
) -> tuple[TestClient, LocalSkillStorage]:
    skills_root = tmp_path / "skills"
    (skills_root / "public").mkdir(parents=True)
    (skills_root / "custom").mkdir(parents=True)
    storage = LocalSkillStorage(host_path=str(skills_root))

    async def allow_scan(*args, **kwargs):
        return ScanResult(decision="allow", reason="ok")

    async def refresh_cache():
        return None

    def user_factory() -> User:
        return User(
            email="router-test@example.com",
            password_hash="x",
            system_role=system_role,  # type: ignore[arg-type]
            id=uuid4(),
        )

    app = make_authed_test_app(user_factory=user_factory)
    app.dependency_overrides[skills_router.get_config] = lambda: SimpleNamespace()
    app.include_router(skills_router.router)

    monkeypatch.setattr(skills_router, "get_or_new_skill_storage", lambda app_config=None: storage)
    monkeypatch.setattr(skills_router, "scan_skill_content", allow_scan)
    monkeypatch.setattr(skills_router, "refresh_skills_system_prompt_cache_async", refresh_cache)
    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", allow_scan)

    return TestClient(app), storage


def _valid_skill_md(name: str, description: str = "Created from the UI") -> str:
    return f"""---
name: {name}
display_name: 测试 Skill（{name}）
description: {description}
---

Use this skill when the user asks for {description.lower()}.
"""


def _make_skill_archive(tmp_path, skill_name: str, suffix: str = ".zip"):
    skill_dir = tmp_path / "build" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_valid_skill_md(skill_name, "Archive skill"), encoding="utf-8")
    archive_path = tmp_path / f"{skill_name}{suffix}"
    with zipfile.ZipFile(archive_path, "w") as zf:
        for file in skill_dir.rglob("*"):
            zf.write(file, file.relative_to(tmp_path / "build"))
    return archive_path


def test_create_custom_skill_writes_skill_md_and_history(tmp_path, monkeypatch):
    client, storage = _make_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/skills/custom",
        json={
            "name": "ui-created-skill",
            "description": "Created from the UI",
            "content": _valid_skill_md("ui-created-skill"),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "ui-created-skill"
    assert body["display_name"] == "测试 Skill（ui-created-skill）"
    assert body["category"] == "custom"
    assert "Created from the UI" in body["content"]
    assert storage.get_custom_skill_file("ui-created-skill").exists()
    assert storage.read_history("ui-created-skill")[0]["action"] == "human_create"


def test_create_custom_skill_rejects_existing_name(tmp_path, monkeypatch):
    client, _storage = _make_client(tmp_path, monkeypatch)
    payload = {
        "name": "dupe-skill",
        "description": "Duplicate",
        "content": _valid_skill_md("dupe-skill", "Duplicate"),
    }

    assert client.post("/api/skills/custom", json=payload).status_code == 200
    response = client.post("/api/skills/custom", json=payload)

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_ai_draft_returns_model_generated_skill_markdown(tmp_path, monkeypatch):
    client, _storage = _make_client(tmp_path, monkeypatch)
    content = _valid_skill_md("research-brief-skill", "Research brief generation")

    async def fake_generate_ai_skill_draft(request, config):
        return skills_router.SkillAIDraftResponse(
            name="research-brief-skill",
            description="Research brief generation",
            content=content,
        )

    monkeypatch.setattr(skills_router, "_generate_ai_skill_draft", fake_generate_ai_skill_draft)

    response = client.post(
        "/api/skills/custom/ai-draft",
        json={"prompt": "Create a skill for generating concise research briefs."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "research-brief-skill"
    assert body["content"] == content


def test_upload_skill_archive_accepts_zip(tmp_path, monkeypatch):
    client, storage = _make_client(tmp_path, monkeypatch)
    archive_path = _make_skill_archive(tmp_path, "uploaded-zip-skill", ".zip")

    with archive_path.open("rb") as file:
        response = client.post(
            "/api/skills/upload",
            files={"file": ("uploaded-zip-skill.zip", file, "application/zip")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["skill_name"] == "uploaded-zip-skill"
    assert storage.get_custom_skill_file("uploaded-zip-skill").exists()


def test_admin_can_read_public_skill_content(tmp_path, monkeypatch):
    client, storage = _make_client(tmp_path, monkeypatch, system_role="admin")
    skill_name = "public-read-skill"
    skill_dir = storage.get_skills_root_path() / "public" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_valid_skill_md(skill_name, "Public skill body"), encoding="utf-8")

    response = client.get(f"/api/skills/public/{skill_name}")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == skill_name
    assert body["category"] == "public"
    assert "Public skill body" in body["content"]


def test_non_admin_cannot_read_public_skill_content(tmp_path, monkeypatch):
    client, storage = _make_client(tmp_path, monkeypatch, system_role="user")
    skill_name = "public-locked-skill"
    skill_dir = storage.get_skills_root_path() / "public" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_valid_skill_md(skill_name), encoding="utf-8")

    response = client.get(f"/api/skills/public/{skill_name}")

    assert response.status_code == 403
