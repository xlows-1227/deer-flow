"""Human override for LLM security blocks.

Covers the state machine (``failed`` -> ``overridden`` -> ``failed``), the
content-hash pinning that makes an override inert after an edit, the
rule-block exclusion, and the HTTP surface (permissions, audit, prompt-cache
refresh).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.deps import get_config
from app.gateway.routers import skills as skills_router
from deerflow.agents.lead_agent.prompt import _exclude_failed_security_skills
from deerflow.skills.security_report import (
    REASON_CODE_LLM_BLOCK,
    REASON_CODE_RULE_BLOCK,
    REASON_CODE_STRUCTURAL,
    STATUS_FAILED,
    STATUS_OVERRIDDEN,
    STATUS_PASSED,
    STATUS_UNSCANNED,
    delete_override,
    effective_warnings,
    mark_skill_unscanned,
    read_security_report,
    read_security_status,
    record_scan_result,
    write_override,
    write_security_report,
)
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.types import Skill

BLOCK_WARNING = {
    "code": "llm_security_flag",
    "severity": "warn",
    "file": "demo-skill/SKILL.md",
    "line": 12,
    "message": "LLM advisory review flagged this file (block, hardcoded_credential).",
    "excerpt_hash": "abcdef0123456789",
    "source": "llm",
    "decision": "block",
    "category": "hardcoded_credential",
}


def _make_skill_dir(tmp_path: Path, name: str = "demo-skill") -> Path:
    skill_dir = tmp_path / "skills" / "custom" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_failed_report(skill_dir: Path, *, reason_code: str = REASON_CODE_LLM_BLOCK, content_hash: str = "hash-1") -> str:
    write_security_report(
        skill_dir,
        content_hash=content_hash,
        warnings=[BLOCK_WARNING],
        status=STATUS_FAILED,
        reason="LLM advisory review flagged demo-skill/SKILL.md as unsafe",
        reason_code=reason_code,
    )
    return content_hash


def _grant(skill_dir: Path, content_hash: str) -> None:
    write_override(
        skill_dir,
        content_hash=content_hash,
        actor={"user_id": "user-1", "email": "owner@example.com", "role": "owner"},
        reason="internal tool",
        acknowledged=[{"code": "llm_security_flag", "decision": "block"}],
    )


def _skill(skill_dir: Path, name: str = "demo-skill") -> Skill:
    return Skill(
        name=name,
        description="Demo",
        license="MIT",
        skill_dir=skill_dir,
        skill_file=skill_dir / "SKILL.md",
        relative_path=Path(name),
        category="custom",
        enabled=True,
    )


# --- state machine ---------------------------------------------------------


def test_override_flips_failed_to_overridden(tmp_path):
    skill_dir = _make_skill_dir(tmp_path)
    content_hash = _write_failed_report(skill_dir)

    _grant(skill_dir, content_hash)
    status = read_security_status(skill_dir)

    assert status["status"] == STATUS_OVERRIDDEN
    assert status["reason_code"] == REASON_CODE_LLM_BLOCK
    assert status["override"]["actor"]["role"] == "owner"
    assert status["override"]["reason"] == "internal tool"


def test_override_is_inert_after_content_change(tmp_path):
    skill_dir = _make_skill_dir(tmp_path)
    content_hash = _write_failed_report(skill_dir)
    _grant(skill_dir, content_hash)

    mark_skill_unscanned(skill_dir, reason="edited")

    assert read_security_status(skill_dir)["status"] == STATUS_UNSCANNED


def test_override_applies_to_rule_blocks(tmp_path):
    """Every security verdict is overridable, not just LLM blocks."""
    skill_dir = _make_skill_dir(tmp_path)
    content_hash = _write_failed_report(skill_dir, reason_code=REASON_CODE_RULE_BLOCK)

    _grant(skill_dir, content_hash)

    assert read_security_status(skill_dir)["status"] == STATUS_OVERRIDDEN


def test_override_applies_to_structural_failures(tmp_path):
    skill_dir = _make_skill_dir(tmp_path)
    content_hash = _write_failed_report(skill_dir, reason_code=REASON_CODE_STRUCTURAL)

    _grant(skill_dir, content_hash)

    assert read_security_status(skill_dir)["status"] == STATUS_OVERRIDDEN


def test_override_requires_matching_hash(tmp_path):
    skill_dir = _make_skill_dir(tmp_path)
    _write_failed_report(skill_dir, content_hash="hash-1")
    _grant(skill_dir, "stale-hash")

    assert read_security_status(skill_dir)["status"] == STATUS_FAILED


def test_revoking_override_returns_to_failed(tmp_path):
    skill_dir = _make_skill_dir(tmp_path)
    content_hash = _write_failed_report(skill_dir)
    _grant(skill_dir, content_hash)

    assert delete_override(skill_dir) is True
    assert read_security_status(skill_dir)["status"] == STATUS_FAILED
    assert delete_override(skill_dir) is False


def test_effective_warnings_exposes_status_and_override(tmp_path):
    skill_dir = _make_skill_dir(tmp_path)
    content_hash = _write_failed_report(skill_dir)
    _grant(skill_dir, content_hash)

    report = effective_warnings(skill_dir)

    assert report["status"] == STATUS_OVERRIDDEN
    assert report["override"]["actor"]["email"] == "owner@example.com"


def test_overridden_skill_is_visible_to_the_model(tmp_path):
    failed_dir = _make_skill_dir(tmp_path, "failed-skill")
    overridden_dir = _make_skill_dir(tmp_path, "overridden-skill")
    _write_failed_report(failed_dir)
    content_hash = _write_failed_report(overridden_dir)
    _grant(overridden_dir, content_hash)

    visible = _exclude_failed_security_skills([_skill(failed_dir, "failed-skill"), _skill(overridden_dir, "overridden-skill")])

    assert [skill.name for skill in visible] == ["overridden-skill"]


def test_rescan_result_with_same_hash_keeps_override(tmp_path):
    """A rescan that reports the same failure must not drop an active override."""
    skill_dir = _make_skill_dir(tmp_path)
    content_hash = _write_failed_report(skill_dir)
    _grant(skill_dir, content_hash)

    record_scan_result(
        skill_dir,
        status=STATUS_FAILED,
        reason="same verdict",
        warnings=[BLOCK_WARNING],
        content_hash=content_hash,
        reason_code=REASON_CODE_LLM_BLOCK,
    )

    assert read_security_status(skill_dir)["status"] == STATUS_OVERRIDDEN


def test_legacy_failed_report_without_reason_code_is_overridable(tmp_path):
    """Reports written before ``reason_code`` existed are still overridable."""
    skill_dir = _make_skill_dir(tmp_path)
    legacy_report = {
        "version": 1,
        "status": STATUS_FAILED,
        "reason": "LLM advisory review flagged demo-skill/SKILL.md as unsafe",
        "content_hash": "legacy-hash",
        "warnings": [BLOCK_WARNING],
    }
    (skill_dir / ".security-scan.json").write_text(json.dumps(legacy_report), encoding="utf-8")

    assert read_security_status(skill_dir)["reason_code"] == REASON_CODE_LLM_BLOCK
    _grant(skill_dir, "legacy-hash")
    assert read_security_status(skill_dir)["status"] == STATUS_OVERRIDDEN


# --- HTTP surface ----------------------------------------------------------


def _config(skills_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        skills=SimpleNamespace(
            get_skills_path=lambda: skills_root,
            container_path="/mnt/skills",
            use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage",
        ),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )


class _EmptyShareRepo:
    """Share-grant stub: every query returns nothing."""

    async def _empty(self, *args: object, **kwargs: object) -> list:
        return []

    def __getattr__(self, name: str):
        return self._empty


def _build_client(monkeypatch, skills_root: Path, *, admin: bool, refresh_calls: list[str]):
    storage = LocalSkillStorage(host_path=str(skills_root))
    monkeypatch.setattr(skills_router, "get_or_new_skill_storage", lambda **kw: storage)

    async def _refresh():
        refresh_calls.append("refresh")

    monkeypatch.setattr(skills_router, "refresh_skills_system_prompt_cache_async", _refresh)
    user = User(
        email="admin@example.com" if admin else "stranger@example.com",
        password_hash="x",
        system_role="admin" if admin else "user",
        id=uuid4(),
    )
    app = make_authed_test_app(user_factory=lambda: user)
    config = _config(skills_root)
    # The custom-skill list needs the share-grants repository; an empty stub
    # keeps the badge assertions running without a database.
    app.state.skill_share_repo = _EmptyShareRepo()
    app.dependency_overrides[get_config] = lambda: config
    app.include_router(skills_router.router)
    return TestClient(app), storage


def test_admin_can_override_and_the_action_is_audited(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = _make_skill_dir(tmp_path)
    _write_failed_report(skill_dir)
    refresh_calls: list[str] = []
    client, storage = _build_client(monkeypatch, skills_root, admin=True, refresh_calls=refresh_calls)

    with client:
        response = client.post("/api/skills/custom/demo-skill/security/override", json={"acknowledge": True, "reason": "test"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == STATUS_OVERRIDDEN
    assert response.json()["override"]["actor"]["role"] == "admin"
    assert refresh_calls == ["refresh"]

    history = storage.read_history("demo-skill")
    granted = [entry for entry in history if entry.get("action") == "security_override_granted"]
    assert len(granted) == 1
    assert granted[0]["content_hash"] == "hash-1"
    assert granted[0]["actor_role"] == "admin"
    assert granted[0]["acknowledged"][0]["category"] == "hardcoded_credential"


def test_non_admin_cannot_override(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = _make_skill_dir(tmp_path)
    _write_failed_report(skill_dir)
    client, _ = _build_client(monkeypatch, skills_root, admin=False, refresh_calls=[])

    with client:
        response = client.post("/api/skills/custom/demo-skill/security/override", json={"acknowledge": True})

    assert response.status_code == 404
    assert read_security_status(skill_dir)["status"] == STATUS_FAILED


def test_override_requires_acknowledgement(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    _write_failed_report(_make_skill_dir(tmp_path))
    client, _ = _build_client(monkeypatch, skills_root, admin=True, refresh_calls=[])

    with client:
        response = client.post("/api/skills/custom/demo-skill/security/override", json={"acknowledge": False})

    assert response.status_code == 400


def test_override_rejected_when_skill_is_not_failed(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = _make_skill_dir(tmp_path)
    write_security_report(skill_dir, content_hash="hash-1", warnings=[], status=STATUS_PASSED)
    client, _ = _build_client(monkeypatch, skills_root, admin=True, refresh_calls=[])

    with client:
        response = client.post("/api/skills/custom/demo-skill/security/override", json={"acknowledge": True})

    assert response.status_code == 409


def test_override_allowed_for_rule_block(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = _make_skill_dir(tmp_path)
    _write_failed_report(skill_dir, reason_code=REASON_CODE_RULE_BLOCK)
    client, _ = _build_client(monkeypatch, skills_root, admin=True, refresh_calls=[])

    with client:
        response = client.post("/api/skills/custom/demo-skill/security/override", json={"acknowledge": True})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == STATUS_OVERRIDDEN
    assert read_security_status(skill_dir)["status"] == STATUS_OVERRIDDEN


def test_admin_can_revoke_an_override(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = _make_skill_dir(tmp_path)
    content_hash = _write_failed_report(skill_dir)
    _grant(skill_dir, content_hash)
    refresh_calls: list[str] = []
    client, storage = _build_client(monkeypatch, skills_root, admin=True, refresh_calls=refresh_calls)

    with client:
        response = client.delete("/api/skills/custom/demo-skill/security/override")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == STATUS_FAILED
    assert read_security_status(skill_dir)["status"] == STATUS_FAILED
    assert refresh_calls == ["refresh"]
    actions = [entry.get("action") for entry in storage.read_history("demo-skill")]
    assert "security_override_revoked" in actions


def test_revoke_without_override_is_rejected(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    _write_failed_report(_make_skill_dir(tmp_path))
    client, _ = _build_client(monkeypatch, skills_root, admin=True, refresh_calls=[])

    with client:
        response = client.delete("/api/skills/custom/demo-skill/security/override")

    assert response.status_code == 409


def test_report_endpoint_returns_override_metadata(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = _make_skill_dir(tmp_path)
    content_hash = _write_failed_report(skill_dir)
    _grant(skill_dir, content_hash)
    client, _ = _build_client(monkeypatch, skills_root, admin=True, refresh_calls=[])

    with client:
        response = client.get("/api/skills/custom/demo-skill/security")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == STATUS_OVERRIDDEN
    assert payload["override"]["actor"]["email"] == "owner@example.com"
    assert read_security_report(skill_dir) is not None


def test_list_endpoint_exposes_overridden_and_failed_badges(monkeypatch, tmp_path):
    """The badge on the skills list is driven by ``security_status``."""
    skills_root = tmp_path / "skills"
    overridden_dir = _make_skill_dir(tmp_path, "overridden-skill")
    _grant(overridden_dir, _write_failed_report(overridden_dir))
    _write_failed_report(_make_skill_dir(tmp_path, "failed-skill"))
    client, _ = _build_client(monkeypatch, skills_root, admin=True, refresh_calls=[])

    with client:
        response = client.get("/api/skills/custom")

    assert response.status_code == 200, response.text
    statuses = {item["name"]: item.get("security_status") for item in response.json()["skills"]}
    assert statuses["overridden-skill"] == STATUS_OVERRIDDEN
    assert statuses["failed-skill"] == STATUS_FAILED
