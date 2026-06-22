import errno
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.deps import get_config
from app.gateway.routers import skills as skills_router
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.skills.types import Skill


def _skill_content(name: str, description: str = "Demo skill") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


async def _async_scan(decision: str, reason: str):
    from deerflow.skills.security_scanner import ScanResult

    return ScanResult(decision=decision, reason=reason)


def _make_skill(name: str, *, enabled: bool) -> Skill:
    skill_dir = Path(f"/tmp/{name}")
    return Skill(
        name=name,
        description=f"Description for {name}",
        license="MIT",
        skill_dir=skill_dir,
        skill_file=skill_dir / "SKILL.md",
        relative_path=Path(name),
        category="public",
        enabled=enabled,
    )


def _make_test_app(config) -> FastAPI:
    app = FastAPI()
    app.state.config = config  # kept for any startup-style reads
    app.dependency_overrides[get_config] = lambda: config
    app.include_router(skills_router.router)
    return app


def _make_skill_archive(tmp_path: Path, name: str, content: str | None = None) -> Path:
    archive = tmp_path / f"{name}.skill"
    skill_content = content or _skill_content(name)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{name}/SKILL.md", skill_content)
    return archive


def test_install_skill_archive_runs_security_scan(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    (skills_root / "custom").mkdir(parents=True)
    archive = _make_skill_archive(tmp_path, "archive-skill")
    scan_calls = []
    refresh_calls = []

    async def _scan(content, *, executable, location, app_config=None):
        from deerflow.skills.security_scanner import ScanResult

        scan_calls.append({"content": content, "executable": executable, "location": location})
        return ScanResult(decision="allow", reason="ok")

    async def _refresh():
        refresh_calls.append("refresh")

    from types import SimpleNamespace

    from deerflow.skills.storage.local_skill_storage import LocalSkillStorage

    storage = LocalSkillStorage(host_path=str(skills_root))
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr(skills_router, "resolve_thread_virtual_path", lambda thread_id, path: archive)
    monkeypatch.setattr(skills_router, "get_or_new_skill_storage", lambda **kw: storage)
    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)
    monkeypatch.setattr(skills_router, "refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        response = client.post("/api/skills/install", json={"thread_id": "thread-1", "path": "mnt/user-data/outputs/archive-skill.skill"})

    assert response.status_code == 200
    assert response.json()["skill_name"] == "archive-skill"
    assert (skills_root / "custom" / "archive-skill" / "SKILL.md").exists()
    assert scan_calls == [
        {
            "content": _skill_content("archive-skill"),
            "executable": False,
            "location": "archive-skill/SKILL.md",
        }
    ]
    assert refresh_calls == ["refresh"]


def test_install_skill_archive_security_scan_block_returns_400(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    (skills_root / "custom").mkdir(parents=True)
    archive = _make_skill_archive(tmp_path, "blocked-skill")
    refresh_calls = []

    async def _scan(*args, **kwargs):
        from deerflow.skills.security_scanner import ScanResult

        return ScanResult(decision="block", reason="prompt injection")

    async def _refresh():
        refresh_calls.append("refresh")

    from types import SimpleNamespace

    from deerflow.skills.storage.local_skill_storage import LocalSkillStorage

    storage = LocalSkillStorage(host_path=str(skills_root))
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr(skills_router, "resolve_thread_virtual_path", lambda thread_id, path: archive)
    monkeypatch.setattr(skills_router, "get_or_new_skill_storage", lambda **kw: storage)
    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)
    monkeypatch.setattr(skills_router, "refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        response = client.post("/api/skills/install", json={"thread_id": "thread-1", "path": "mnt/user-data/outputs/blocked-skill.skill"})

    assert response.status_code == 400
    assert "Security scan blocked skill 'blocked-skill': prompt injection" in response.json()["detail"]
    assert not (skills_root / "custom" / "blocked-skill").exists()
    assert refresh_calls == []


def test_upload_skill_archive_security_scan_block_returns_reason(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    (skills_root / "custom").mkdir(parents=True)
    refresh_calls = []

    async def _scan(*args, **kwargs):
        from deerflow.skills.security_scanner import ScanResult

        return ScanResult(decision="block", reason="prompt injection")

    async def _refresh():
        refresh_calls.append("refresh")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)
    monkeypatch.setattr(skills_router, "refresh_skills_system_prompt_cache_async", _refresh)

    archive = _make_skill_archive(tmp_path, "blocked-skill")
    app = _make_test_app(config)

    with TestClient(app) as client, archive.open("rb") as file:
        response = client.post("/api/skills/upload", files={"file": ("blocked-skill.skill", file, "application/zip")})

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "security_scan_failed",
        "message": "Security scan blocked skill 'blocked-skill': prompt injection",
        "reason": "prompt injection",
        "can_force": True,
    }
    assert not (skills_root / "custom" / "blocked-skill").exists()
    assert refresh_calls == []


def test_upload_skill_archive_force_skips_security_scan(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    (skills_root / "custom").mkdir(parents=True)
    refresh_calls = []

    async def _scan(*args, **kwargs):
        from deerflow.skills.security_scanner import ScanResult

        return ScanResult(decision="block", reason="prompt injection")

    async def _refresh():
        refresh_calls.append("refresh")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)
    monkeypatch.setattr(skills_router, "refresh_skills_system_prompt_cache_async", _refresh)

    archive = _make_skill_archive(tmp_path, "forced-skill")
    app = _make_test_app(config)

    with TestClient(app) as client, archive.open("rb") as file:
        response = client.post("/api/skills/upload?force=true", files={"file": ("forced-skill.skill", file, "application/zip")})

    assert response.status_code == 200
    assert response.json()["skill_name"] == "forced-skill"
    assert (skills_root / "custom" / "forced-skill" / "SKILL.md").exists()
    assert refresh_calls == ["refresh"]


def test_custom_skills_router_lifecycle(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    custom_dir = skills_root / "custom" / "demo-skill"
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", lambda *args, **kwargs: _async_scan("allow", "ok"))
    refresh_calls = []

    async def _refresh():
        refresh_calls.append("refresh")

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        response = client.get("/api/skills/custom")
        assert response.status_code == 200
        assert response.json()["skills"][0]["name"] == "demo-skill"

        get_response = client.get("/api/skills/custom/demo-skill")
        assert get_response.status_code == 200
        assert "# demo-skill" in get_response.json()["content"]

        update_response = client.put(
            "/api/skills/custom/demo-skill",
            json={"content": _skill_content("demo-skill", "Edited skill")},
        )
        assert update_response.status_code == 200
        assert update_response.json()["description"] == "Edited skill"

        history_response = client.get("/api/skills/custom/demo-skill/history")
        assert history_response.status_code == 200
        assert history_response.json()["history"][-1]["action"] == "human_edit"

        rollback_response = client.post("/api/skills/custom/demo-skill/rollback", json={"history_index": -1})
        assert rollback_response.status_code == 200
        assert rollback_response.json()["description"] == "Demo skill"
        assert refresh_calls == ["refresh", "refresh"]


def test_custom_skill_delete_support_file_records_history(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    custom_dir = skills_root / "custom" / "demo-skill"
    support_file = custom_dir / "skills" / "notes.md"
    support_file.parent.mkdir(parents=True, exist_ok=True)
    (custom_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")
    support_file.write_text("notes", encoding="utf-8")
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    refresh_calls = []

    async def _refresh():
        refresh_calls.append("refresh")

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        response = client.delete("/api/skills/custom/demo-skill/file?path=skills%2Fnotes.md")
        assert response.status_code == 200
        assert response.json() == {"success": True}
        assert not support_file.exists()

        history_response = client.get("/api/skills/custom/demo-skill/history")
        assert history_response.status_code == 200
        history = history_response.json()["history"]
        assert history[-1]["action"] == "human_delete_file"
        assert history[-1]["file_path"] == "skills/notes.md"
        assert history[-1]["prev_content"] == "notes"
        assert refresh_calls == ["refresh"]


def test_custom_skill_rollback_blocked_by_scanner(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    custom_dir = skills_root / "custom" / "demo-skill"
    custom_dir.mkdir(parents=True, exist_ok=True)
    original_content = _skill_content("demo-skill")
    edited_content = _skill_content("demo-skill", "Edited skill")
    (custom_dir / "SKILL.md").write_text(edited_content, encoding="utf-8")
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    history_file = get_or_new_skill_storage(app_config=config).get_skill_history_file("demo-skill")
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(
        '{"action":"human_edit","prev_content":' + json.dumps(original_content) + ',"new_content":' + json.dumps(edited_content) + "}\n",
        encoding="utf-8",
    )

    async def _refresh():
        return None

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    async def _scan(*args, **kwargs):
        from deerflow.skills.security_scanner import ScanResult

        return ScanResult(decision="block", reason="unsafe rollback")

    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", _scan)

    app = _make_test_app(config)

    with TestClient(app) as client:
        rollback_response = client.post("/api/skills/custom/demo-skill/rollback", json={"history_index": -1})
        assert rollback_response.status_code == 400
        assert "unsafe rollback" in rollback_response.json()["detail"]

        history_response = client.get("/api/skills/custom/demo-skill/history")
        assert history_response.status_code == 200
        assert history_response.json()["history"][-1]["scanner"]["decision"] == "block"


def test_custom_skill_delete_preserves_history_and_allows_restore(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    custom_dir = skills_root / "custom" / "demo-skill"
    custom_dir.mkdir(parents=True, exist_ok=True)
    original_content = _skill_content("demo-skill")
    (custom_dir / "SKILL.md").write_text(original_content, encoding="utf-8")
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", lambda *args, **kwargs: _async_scan("allow", "ok"))
    refresh_calls = []

    async def _refresh():
        refresh_calls.append("refresh")

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        delete_response = client.delete("/api/skills/custom/demo-skill")
        assert delete_response.status_code == 200
        assert not (custom_dir / "SKILL.md").exists()

        history_response = client.get("/api/skills/custom/demo-skill/history")
        assert history_response.status_code == 200
        assert history_response.json()["history"][-1]["action"] == "human_delete"

        rollback_response = client.post("/api/skills/custom/demo-skill/rollback", json={"history_index": -1})
        assert rollback_response.status_code == 200
        assert rollback_response.json()["description"] == "Demo skill"
        assert (custom_dir / "SKILL.md").read_text(encoding="utf-8") == original_content
        assert refresh_calls == ["refresh", "refresh"]


def test_custom_skill_delete_continues_when_history_write_is_readonly(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    custom_dir = skills_root / "custom" / "demo-skill"
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    refresh_calls = []

    async def _refresh():
        refresh_calls.append("refresh")

    def _readonly_history(*args, **kwargs):
        raise OSError(errno.EROFS, "Read-only file system", str(skills_root / "custom" / ".history"))

    monkeypatch.setattr("deerflow.skills.storage.local_skill_storage.LocalSkillStorage.append_history", _readonly_history)
    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        delete_response = client.delete("/api/skills/custom/demo-skill")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"success": True}
    assert not custom_dir.exists()
    assert refresh_calls == ["refresh"]


def test_custom_skill_delete_fails_when_skill_dir_removal_fails(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    custom_dir = skills_root / "custom" / "demo-skill"
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    refresh_calls = []

    async def _refresh():
        refresh_calls.append("refresh")

    def _fail_rmtree(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", str(custom_dir))

    monkeypatch.setattr("deerflow.skills.storage.local_skill_storage.shutil.rmtree", _fail_rmtree)
    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        delete_response = client.delete("/api/skills/custom/demo-skill")

    assert delete_response.status_code == 500
    assert "Failed to delete custom skill" in delete_response.json()["detail"]
    assert custom_dir.exists()
    assert refresh_calls == []


def test_update_skill_refreshes_prompt_cache_before_return(monkeypatch, tmp_path):
    config_path = tmp_path / "extensions_config.json"
    enabled_state = {"value": True}
    refresh_calls = []

    def _load_skills(*, enabled_only: bool):
        skill = _make_skill("demo-skill", enabled=enabled_state["value"])
        if enabled_only and not skill.enabled:
            return []
        return [skill]

    async def _refresh():
        refresh_calls.append("refresh")
        enabled_state["value"] = False

    mock_storage = SimpleNamespace(load_skills=_load_skills)
    monkeypatch.setattr("app.gateway.routers.skills.get_or_new_skill_storage", lambda **kwargs: mock_storage)
    monkeypatch.setattr("app.gateway.routers.skills.get_extensions_config", lambda: SimpleNamespace(mcp_servers={}, skills={}))
    monkeypatch.setattr("app.gateway.routers.skills.reload_extensions_config", lambda: None)
    monkeypatch.setattr(skills_router.ExtensionsConfig, "resolve_config_path", staticmethod(lambda: config_path))
    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(SimpleNamespace())

    with TestClient(app) as client:
        response = client.put("/api/skills/demo-skill", json={"enabled": False})

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert refresh_calls == ["refresh"]
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"mcpServers": {}, "skills": {"demo-skill": {"enabled": False}}}


def test_list_custom_skill_files_returns_tree_entries(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "custom" / "demo-skill"
    refs_dir = skill_dir / "references"
    scripts_dir = skill_dir / "scripts"
    refs_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")
    (refs_dir / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (scripts_dir / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)

    app = _make_test_app(config)
    with TestClient(app) as client:
        response = client.get("/api/skills/custom/demo-skill/files")

    assert response.status_code == 200
    payload = response.json()
    paths = {entry["path"]: entry["type"] for entry in payload["files"]}
    assert paths["SKILL.md"] == "file"
    assert paths["references"] == "directory"
    assert paths["references/guide.md"] == "file"
    assert paths["scripts"] == "directory"
    assert paths["scripts/run.sh"] == "file"


def test_read_custom_skill_file_allows_in_progress_public_override(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    public_dir = skills_root / "public" / "github-repo-analyzer"
    custom_dir = skills_root / "custom" / "github-repo-analyzer" / "skills"
    public_dir.mkdir(parents=True)
    custom_dir.mkdir(parents=True)
    (public_dir / "SKILL.md").write_text(_skill_content("github-repo-analyzer"), encoding="utf-8")
    (custom_dir / "github-repo-analyzer.skill").write_bytes(b"PK\x03\x04binary")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)

    app = _make_test_app(config)
    with TestClient(app) as client:
        list_response = client.get("/api/skills/custom/github-repo-analyzer/files")
        read_response = client.get(
            "/api/skills/custom/github-repo-analyzer/file",
            params={"path": "skills/github-repo-analyzer.skill"},
        )

    assert list_response.status_code == 200
    assert read_response.status_code == 400
    assert "cannot be read as text" in read_response.json()["detail"]

    custom_skill_dir = skills_root / "custom" / "github-repo-analyzer"
    refs_dir = custom_skill_dir / "references"
    refs_dir.mkdir(parents=True)
    (refs_dir / "guide.md").write_text("# Guide\n", encoding="utf-8")

    with TestClient(app) as client:
        text_response = client.get(
            "/api/skills/custom/github-repo-analyzer/file",
            params={"path": "references/guide.md"},
        )

    assert text_response.status_code == 200
    assert text_response.json()["content"] == "# Guide\n"


def test_read_custom_skill_file_returns_supporting_file(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "custom" / "demo-skill"
    refs_dir = skill_dir / "references"
    refs_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")
    (refs_dir / "guide.md").write_text("# Guide\n", encoding="utf-8")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)

    app = _make_test_app(config)
    with TestClient(app) as client:
        response = client.get("/api/skills/custom/demo-skill/file", params={"path": "references/guide.md"})

    assert response.status_code == 200
    assert response.json()["path"] == "references/guide.md"
    assert response.json()["content"] == "# Guide\n"


def test_write_custom_skill_support_file(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "custom" / "demo-skill"
    refs_dir = skill_dir / "references"
    refs_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", lambda *args, **kwargs: _async_scan("allow", "ok"))

    async def _refresh():
        return None

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)
    with TestClient(app) as client:
        response = client.put(
            "/api/skills/custom/demo-skill/file",
            json={"path": "references/notes.md", "content": "# Notes\n"},
        )

    assert response.status_code == 200
    assert (refs_dir / "notes.md").read_text(encoding="utf-8") == "# Notes\n"


def test_create_custom_skill_directory_bootstraps_public_override(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    public_dir = skills_root / "public" / "github-repo-analyzer"
    public_dir.mkdir(parents=True)
    (public_dir / "SKILL.md").write_text(_skill_content("github-repo-analyzer"), encoding="utf-8")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)

    app = _make_test_app(config)
    with TestClient(app) as client:
        response = client.post(
            "/api/skills/custom/github-repo-analyzer/directories",
            json={"path": "scripts"},
        )

    assert response.status_code == 200
    assert (skills_root / "custom" / "github-repo-analyzer" / "scripts").is_dir()


def test_create_custom_skill_directory(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "custom" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)

    app = _make_test_app(config)
    with TestClient(app) as client:
        response = client.post(
            "/api/skills/custom/demo-skill/directories",
            json={"path": "references/subdir"},
        )

    assert response.status_code == 200
    assert (skill_dir / "references" / "subdir").is_dir()


def test_upload_custom_skill_files(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "custom" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", lambda *args, **kwargs: _async_scan("allow", "ok"))

    async def _refresh():
        return None

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)
    with TestClient(app) as client:
        response = client.post(
            "/api/skills/custom/demo-skill/upload",
            files=[("files", ("data.txt", b"hello", "text/plain"))],
            data={"paths": "references/data.txt"},
        )

    assert response.status_code == 200
    assert response.json()["paths"] == ["references/data.txt"]
    assert (skill_dir / "references" / "data.txt").read_text(encoding="utf-8") == "hello"


def test_create_custom_skill_creates_initial_version(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    (skills_root / "custom").mkdir(parents=True)
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", lambda *args, **kwargs: _async_scan("allow", "ok"))

    async def _refresh():
        return None

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)
    with TestClient(app) as client:
        response = client.post(
            "/api/skills/custom",
            json={"name": "versioned-skill", "description": "Versioned skill"},
        )
        assert response.status_code == 200

        versions_response = client.get("/api/skills/custom/versioned-skill/versions")
        assert versions_response.status_code == 200
        versions = versions_response.json()["versions"]
        assert len(versions) == 1
        assert versions[0]["seq"] == 1
        assert versions[0]["action"] == "create"


def test_custom_skill_versions_api_lifecycle(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "custom" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_content("demo-skill"), encoding="utf-8")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "notes.md").write_text("notes", encoding="utf-8")

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills", use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", lambda *args, **kwargs: _async_scan("allow", "ok"))

    async def _refresh():
        return None

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    storage = get_or_new_skill_storage(app_config=config)
    storage.create_skill_version("demo-skill", action="create", author="human")
    storage.write_custom_skill("demo-skill", "SKILL.md", _skill_content("demo-skill", "Edited"))
    storage.write_custom_skill("demo-skill", "references/notes.md", "edited notes")

    app = _make_test_app(config)
    with TestClient(app) as client:
        list_response = client.get("/api/skills/custom/demo-skill/versions")
        assert list_response.status_code == 200
        assert list_response.json()["versions"][0]["seq"] == 1

        snapshot_response = client.post(
            "/api/skills/custom/demo-skill/versions",
            json={"action": "edit", "message": "manual snapshot"},
        )
        assert snapshot_response.status_code == 200
        assert snapshot_response.json()["seq"] == 2

        files_response = client.get("/api/skills/custom/demo-skill/versions/1/files")
        assert files_response.status_code == 200
        paths = {entry["path"] for entry in files_response.json()["files"] if entry["type"] == "file"}
        assert "SKILL.md" in paths
        assert "references/notes.md" in paths

        file_response = client.get("/api/skills/custom/demo-skill/versions/1/file?path=references%2Fnotes.md")
        assert file_response.status_code == 200
        assert file_response.json()["content"] == "notes"

        restore_response = client.post("/api/skills/custom/demo-skill/versions/1/restore")
        assert restore_response.status_code == 200
        assert restore_response.json()["version"]["restored_from"] == 1

        get_response = client.get("/api/skills/custom/demo-skill")
        assert get_response.status_code == 200
        assert get_response.json()["description"] == "Demo skill"
        assert (skill_dir / "references" / "notes.md").read_text(encoding="utf-8") == "notes"


def test_other_users_cannot_probe_custom_skill_history_or_versions(monkeypatch, tmp_path):
    from deerflow.skills.storage.local_skill_storage import LocalSkillStorage

    skills_root = tmp_path / "skills"
    current_user = {"id": "user-a"}
    storage = LocalSkillStorage(
        host_path=str(skills_root),
        enforce_owner_isolation=True,
    )
    monkeypatch.setattr(
        storage,
        "_current_user_id",
        lambda: current_user["id"],
    )
    storage.write_custom_skill(
        "private-skill",
        "SKILL.md",
        _skill_content("private-skill"),
    )
    storage.append_history(
        "private-skill",
        {"action": "create", "author": "human"},
    )
    storage.create_skill_version(
        "private-skill",
        action="create",
        author="human",
    )
    current_user["id"] = "user-b"

    config = SimpleNamespace()
    monkeypatch.setattr(
        skills_router,
        "get_or_new_skill_storage",
        lambda **kwargs: storage,
    )
    app = _make_test_app(config)

    with TestClient(app) as client:
        history_response = client.get(
            "/api/skills/custom/private-skill/history",
        )
        versions_response = client.get(
            "/api/skills/custom/private-skill/versions",
        )

    assert history_response.status_code == 404
    assert versions_response.status_code == 404
