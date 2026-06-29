"""Tests for LocalSkillStorage version snapshot management."""

from __future__ import annotations

import stat

from deerflow.skills.storage import get_or_new_skill_storage


def _skill_content(name: str, description: str = "Demo skill", version: str | None = None) -> str:
    version_line = f"version: {version}\n" if version else ""
    return f"---\nname: {name}\ndescription: {description}\n{version_line}---\n\n# {name}\n"


def _setup_skill(storage, name: str, *, description: str = "Demo skill", version: str | None = None) -> None:
    content = _skill_content(name, description, version)
    storage.write_custom_skill(name, "SKILL.md", content)
    storage.write_custom_skill(name, "references/notes.md", "supporting notes")


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_create_skill_version_starts_at_seq_one(tmp_path):
    storage = get_or_new_skill_storage(skills_path=str(tmp_path))
    _setup_skill(storage, "demo-skill")

    record = storage.create_skill_version("demo-skill", action="create", author="human")

    assert record["seq"] == 1
    assert record["action"] == "create"
    assert record["file_count"] == 2
    assert (tmp_path / "custom" / ".versions" / "demo-skill" / "1" / "SKILL.md").exists()
    assert (tmp_path / "custom" / ".versions" / "demo-skill" / "1" / "references" / "notes.md").exists()


def test_create_skill_version_increments_seq(tmp_path):
    storage = get_or_new_skill_storage(skills_path=str(tmp_path))
    _setup_skill(storage, "demo-skill")

    first = storage.create_skill_version("demo-skill", action="create", author="human")
    storage.write_custom_skill("demo-skill", "SKILL.md", _skill_content("demo-skill", "Edited"))
    second = storage.create_skill_version("demo-skill", action="edit", author="human")

    assert first["seq"] == 1
    assert second["seq"] == 2
    versions = storage.list_skill_versions("demo-skill")
    assert [item["seq"] for item in versions] == [2, 1]


def test_create_skill_version_keeps_only_latest_five_snapshots(tmp_path):
    storage = get_or_new_skill_storage(skills_path=str(tmp_path))
    _setup_skill(storage, "demo-skill")

    for index in range(7):
        storage.write_custom_skill("demo-skill", "SKILL.md", _skill_content("demo-skill", f"Edit {index}"))
        storage.create_skill_version("demo-skill", action="edit", author="human")

    versions = storage.list_skill_versions("demo-skill")
    assert [item["seq"] for item in versions] == [7, 6, 5, 4, 3]

    versions_root = tmp_path / "custom" / ".versions" / "demo-skill"
    assert not (versions_root / "1").exists()
    assert not (versions_root / "2").exists()
    for seq in range(3, 8):
        assert (versions_root / str(seq)).exists()


def test_create_skill_version_extracts_frontmatter_label(tmp_path):
    storage = get_or_new_skill_storage(skills_path=str(tmp_path))
    _setup_skill(storage, "demo-skill", version="1.2.3")

    record = storage.create_skill_version("demo-skill", action="create", author="human")

    assert record["label"] == "1.2.3"


def test_read_skill_version_file(tmp_path):
    storage = get_or_new_skill_storage(skills_path=str(tmp_path))
    _setup_skill(storage, "demo-skill")
    storage.create_skill_version("demo-skill", action="create", author="human")

    content = storage.read_skill_version_file("demo-skill", 1, "references/notes.md")

    assert content == "supporting notes"


def test_restore_skill_version_snapshots_current_state_first(tmp_path):
    storage = get_or_new_skill_storage(skills_path=str(tmp_path))
    _setup_skill(storage, "demo-skill")
    storage.create_skill_version("demo-skill", action="create", author="human")

    storage.write_custom_skill("demo-skill", "SKILL.md", _skill_content("demo-skill", "Edited"))
    storage.write_custom_skill("demo-skill", "references/notes.md", "edited notes")

    restored = storage.restore_skill_version("demo-skill", 1, author="human")

    assert storage.read_custom_skill("demo-skill") == _skill_content("demo-skill")
    assert storage.read_custom_skill_file("demo-skill", "references/notes.md") == "supporting notes"
    assert restored["action"] == "restore"
    assert restored["restored_from"] == 1

    versions = storage.list_skill_versions("demo-skill")
    assert [item["seq"] for item in versions] == [3, 2, 1]
    assert versions[1]["action"] == "restore"
    assert "pre-restore snapshot" in (versions[1]["message"] or "")


def test_restore_oldest_retained_version_prunes_after_restore(tmp_path):
    storage = get_or_new_skill_storage(skills_path=str(tmp_path))
    _setup_skill(storage, "demo-skill")

    for index in range(5):
        storage.write_custom_skill("demo-skill", "SKILL.md", _skill_content("demo-skill", f"Edit {index}"))
        storage.create_skill_version("demo-skill", action="edit", author="human")

    restored = storage.restore_skill_version("demo-skill", 1, author="human")

    assert restored["seq"] == 7
    assert restored["restored_from"] == 1
    assert storage.read_custom_skill("demo-skill") == _skill_content("demo-skill", "Edit 0")

    versions = storage.list_skill_versions("demo-skill")
    assert [item["seq"] for item in versions] == [7, 6, 5, 4, 3]

    versions_root = tmp_path / "custom" / ".versions" / "demo-skill"
    assert not (versions_root / "1").exists()
    assert not (versions_root / "2").exists()


def test_restore_preserves_history_and_versions_dirs(tmp_path):
    storage = get_or_new_skill_storage(skills_path=str(tmp_path))
    _setup_skill(storage, "demo-skill")
    storage.append_history("demo-skill", {"action": "human_edit", "author": "human"})
    storage.create_skill_version("demo-skill", action="create", author="human")
    storage.write_custom_skill("demo-skill", "SKILL.md", _skill_content("demo-skill", "Edited"))

    storage.restore_skill_version("demo-skill", 1, author="human")

    assert storage.read_history("demo-skill")
    assert (tmp_path / "custom" / ".history" / "demo-skill.jsonl").exists()
    assert (tmp_path / "custom" / ".versions" / "demo-skill" / "index.jsonl").exists()
    assert (tmp_path / "custom" / ".versions" / "demo-skill" / "1").exists()


def test_restore_normalizes_legacy_restricted_permissions(tmp_path):
    storage = get_or_new_skill_storage(skills_path=str(tmp_path))
    _setup_skill(storage, "demo-skill")
    storage.create_skill_version("demo-skill", action="create", author="human")

    version_dir = tmp_path / "custom" / ".versions" / "demo-skill" / "1"
    (version_dir / "SKILL.md").chmod(0o600)
    (version_dir / "references").chmod(0o700)
    (version_dir / "references" / "notes.md").chmod(0o600)

    storage.restore_skill_version("demo-skill", 1, author="human")

    skill_dir = tmp_path / "custom" / "demo-skill"
    assert _mode(skill_dir) == 0o755
    assert _mode(skill_dir / "SKILL.md") == 0o644
    assert _mode(skill_dir / "references") == 0o755
    assert _mode(skill_dir / "references" / "notes.md") == 0o644
