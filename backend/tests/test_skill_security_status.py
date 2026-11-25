"""Security-status state machine: persistence, rescan selection, visibility."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.lead_agent.prompt import _exclude_failed_security_skills
from deerflow.skills.installer import ScreeningOutcome
from deerflow.skills.security_report import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_UNSCANNED,
    mark_skill_unscanned,
    read_security_status,
    record_scan_result,
    write_security_report,
)
from deerflow.skills.security_rescan import (
    list_unscanned_skill_dirs,
    rescan_skill_dir,
    rescan_unscanned_skills,
)
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.types import Skill, SkillCategory


def _make_skill_dir(tmp_path: Path, name: str = "demo-skill") -> Path:
    skill_dir = tmp_path / "skills" / "custom" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_missing_report_is_treated_as_passed_for_legacy_skills(tmp_path):
    skill_dir = _make_skill_dir(tmp_path)

    status = read_security_status(skill_dir)

    assert status == {"status": STATUS_PASSED, "reason": None, "checked_at": None}


def test_status_transitions_are_persisted_and_readable(tmp_path):
    skill_dir = _make_skill_dir(tmp_path)

    write_security_report(skill_dir, content_hash="hash-1", warnings=[], status=STATUS_PASSED)
    assert read_security_status(skill_dir)["status"] == STATUS_PASSED

    record_scan_result(
        skill_dir,
        status=STATUS_FAILED,
        reason="llm flagged prompt injection",
        warnings=[{"code": "llm_security_flag", "severity": "warn", "message": "flagged"}],
    )
    failed = read_security_status(skill_dir)
    assert failed["status"] == STATUS_FAILED
    assert failed["reason"] == "llm flagged prompt injection"

    mark_skill_unscanned(skill_dir, reason="edited")
    unscanned = read_security_status(skill_dir)
    assert unscanned["status"] == STATUS_UNSCANNED
    assert unscanned["reason"] == "edited"


def test_only_unscanned_skills_enter_the_rescan_queue(tmp_path):
    skills_root = tmp_path / "skills"
    passed_dir = _make_skill_dir(tmp_path, "passed-skill")
    failed_dir = _make_skill_dir(tmp_path, "failed-skill")
    unscanned_dir = _make_skill_dir(tmp_path, "unscanned-skill")
    legacy_dir = _make_skill_dir(tmp_path, "legacy-skill")

    write_security_report(passed_dir, content_hash="h", warnings=[], status=STATUS_PASSED)
    write_security_report(failed_dir, content_hash="h", warnings=[], status=STATUS_FAILED)
    mark_skill_unscanned(unscanned_dir)

    assert list_unscanned_skill_dirs(skills_root) == [unscanned_dir]
    assert read_security_status(legacy_dir)["status"] == STATUS_PASSED


def test_rescan_skips_passed_and_failed(tmp_path, monkeypatch):
    skill_dir = _make_skill_dir(tmp_path)
    write_security_report(skill_dir, content_hash="h", warnings=[], status=STATUS_PASSED)

    calls: list[Path] = []

    async def _screen(path: Path, name: str, **_: object) -> ScreeningOutcome:
        calls.append(path)
        return ScreeningOutcome(warnings=[], status=STATUS_PASSED, reason=None)

    monkeypatch.setattr("deerflow.skills.security_rescan._screen_skill_archive_contents", _screen)

    result = asyncio.run(rescan_skill_dir(skill_dir))

    assert result["scanned"] is False
    assert calls == []


def test_rescan_promotes_unscanned_to_passed(tmp_path, monkeypatch):
    skill_dir = _make_skill_dir(tmp_path)
    mark_skill_unscanned(skill_dir)

    async def _screen(path: Path, name: str, **_: object) -> ScreeningOutcome:
        return ScreeningOutcome(warnings=[], status=STATUS_PASSED, reason=None)

    monkeypatch.setattr("deerflow.skills.security_rescan._screen_skill_archive_contents", _screen)

    result = asyncio.run(rescan_skill_dir(skill_dir))

    assert result == {"scanned": True, "status": STATUS_PASSED, "reason": None, "changed": True}
    assert read_security_status(skill_dir)["status"] == STATUS_PASSED


def test_rescan_does_not_scan_the_same_skill_twice_concurrently(tmp_path, monkeypatch):
    skill_dir = _make_skill_dir(tmp_path)
    mark_skill_unscanned(skill_dir)
    calls = 0

    async def _screen(path: Path, name: str, **_: object) -> ScreeningOutcome:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        return ScreeningOutcome(warnings=[], status=STATUS_PASSED, reason=None)

    monkeypatch.setattr("deerflow.skills.security_rescan._screen_skill_archive_contents", _screen)

    async def _run() -> list[dict]:
        return await asyncio.gather(rescan_skill_dir(skill_dir), rescan_skill_dir(skill_dir))

    first, second = asyncio.run(_run())

    assert calls == 1
    assert {first["scanned"], second["scanned"]} == {True, False}


def test_rescan_keeps_skill_unscanned_when_content_changes_mid_scan(tmp_path, monkeypatch):
    skill_dir = _make_skill_dir(tmp_path)
    mark_skill_unscanned(skill_dir)

    async def _screen(path: Path, name: str, **_: object) -> ScreeningOutcome:
        (path / "references").mkdir()
        (path / "references" / "new.md").write_text("edited during scan", encoding="utf-8")
        return ScreeningOutcome(warnings=[], status=STATUS_PASSED, reason=None)

    monkeypatch.setattr("deerflow.skills.security_rescan._screen_skill_archive_contents", _screen)

    result = asyncio.run(rescan_skill_dir(skill_dir))

    assert result["scanned"] is False
    assert read_security_status(skill_dir)["status"] == STATUS_UNSCANNED


def test_rescan_batch_is_bounded(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    for index in range(5):
        mark_skill_unscanned(_make_skill_dir(tmp_path, f"skill-{index}"))

    async def _screen(path: Path, name: str, **_: object) -> ScreeningOutcome:
        return ScreeningOutcome(warnings=[], status=STATUS_PASSED, reason=None)

    monkeypatch.setattr("deerflow.skills.security_rescan._screen_skill_archive_contents", _screen)

    summary = asyncio.run(rescan_unscanned_skills(skills_root, batch_size=2))

    assert summary["candidates"] == 5
    assert summary["processed"] == 2


def test_rescan_refreshes_prompt_cache_when_a_status_flips(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    flipped = _make_skill_dir(tmp_path, "flipped-skill")
    offline = _make_skill_dir(tmp_path, "offline-skill")
    mark_skill_unscanned(flipped)
    mark_skill_unscanned(offline)

    async def _screen(path: Path, name: str, **_: object) -> ScreeningOutcome:
        if name == "offline-skill":
            return ScreeningOutcome(warnings=[], status=STATUS_UNSCANNED, reason="model unavailable")
        return ScreeningOutcome(warnings=[], status=STATUS_FAILED, reason="llm flagged")

    refreshes: list[bool] = []

    async def _refresh() -> None:
        refreshes.append(True)

    monkeypatch.setattr("deerflow.skills.security_rescan._screen_skill_archive_contents", _screen)
    monkeypatch.setattr("deerflow.agents.lead_agent.prompt.refresh_skills_system_prompt_cache_async", _refresh)

    summary = asyncio.run(rescan_unscanned_skills(skills_root, batch_size=10))

    assert summary["changed"] == 1
    assert refreshes == [True]


def test_rescan_does_not_refresh_prompt_cache_without_a_status_change(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    mark_skill_unscanned(_make_skill_dir(tmp_path, "offline-skill"))

    async def _screen(path: Path, name: str, **_: object) -> ScreeningOutcome:
        return ScreeningOutcome(warnings=[], status=STATUS_UNSCANNED, reason="model unavailable")

    refreshes: list[bool] = []

    async def _refresh() -> None:
        refreshes.append(True)

    monkeypatch.setattr("deerflow.skills.security_rescan._screen_skill_archive_contents", _screen)
    monkeypatch.setattr("deerflow.agents.lead_agent.prompt.refresh_skills_system_prompt_cache_async", _refresh)

    summary = asyncio.run(rescan_unscanned_skills(skills_root, batch_size=10))

    assert summary["changed"] == 0
    assert refreshes == []


def _skill(name: str, skill_dir: Path) -> Skill:
    return Skill(
        name=name,
        description="demo",
        license=None,
        skill_dir=skill_dir,
        skill_file=skill_dir / "SKILL.md",
        relative_path=Path(name),
        category=SkillCategory.CUSTOM,
        enabled=True,
    )


def test_failed_skills_are_excluded_from_prompt_injection(tmp_path):
    passed_dir = _make_skill_dir(tmp_path, "passed-skill")
    failed_dir = _make_skill_dir(tmp_path, "failed-skill")
    unscanned_dir = _make_skill_dir(tmp_path, "unscanned-skill")
    legacy_dir = _make_skill_dir(tmp_path, "legacy-skill")

    write_security_report(passed_dir, content_hash="h", warnings=[], status=STATUS_PASSED)
    write_security_report(failed_dir, content_hash="h", warnings=[], status=STATUS_FAILED)
    mark_skill_unscanned(unscanned_dir)

    visible = _exclude_failed_security_skills(
        [
            _skill("passed-skill", passed_dir),
            _skill("failed-skill", failed_dir),
            _skill("unscanned-skill", unscanned_dir),
            _skill("legacy-skill", legacy_dir),
        ]
    )

    assert [skill.name for skill in visible] == [
        "passed-skill",
        "unscanned-skill",
        "legacy-skill",
    ]


def test_edit_save_resets_status_to_unscanned_without_calling_the_model(tmp_path):
    """Writing a support file resets the status; no LLM call is made on save."""
    from app.gateway.routers.skills import _write_custom_skill_support_file

    skills_root = tmp_path / "skills"
    skill_dir = _make_skill_dir(tmp_path, "demo-skill")
    write_security_report(skill_dir, content_hash="h", warnings=[], status=STATUS_PASSED)
    storage = LocalSkillStorage(host_path=str(skills_root))

    normalized = asyncio.run(
        _write_custom_skill_support_file(
            storage=storage,
            skill_name="demo-skill",
            path="references/notes.md",
            content="# Notes\n",
            config=SimpleNamespace(),
            author="human",
        )
    )

    assert normalized == "references/notes.md"
    status = read_security_status(skill_dir)
    assert status["status"] == STATUS_UNSCANNED
    assert status["reason"] == "content changed; pending background rescan"
    history = storage.read_history("demo-skill")
    assert history[-1]["scanner"]["status"] == STATUS_UNSCANNED


def test_edit_save_still_blocks_structural_rules(tmp_path):
    from fastapi import HTTPException

    from app.gateway.routers.skills import _write_custom_skill_support_file

    skill_dir = _make_skill_dir(tmp_path, "demo-skill")
    storage = LocalSkillStorage(host_path=str(tmp_path / "skills"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            _write_custom_skill_support_file(
                storage=storage,
                skill_name="demo-skill",
                path="scripts/run.sh",
                content="rm -rf /\n",
                config=SimpleNamespace(),
                author="human",
            )
        )

    assert exc_info.value.status_code == 400
    assert "destructive_command" in str(exc_info.value.detail)
    assert not (skill_dir / "scripts" / "run.sh").exists()
