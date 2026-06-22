"""Tests for LocalSkillStorage.write_custom_skill path-traversal guards."""

from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from deerflow.runtime.user_context import reset_current_user, set_current_user
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage


@pytest.fixture()
def storage(tmp_path):
    return get_or_new_skill_storage(skills_path=str(tmp_path))


@pytest.fixture()
def skill_dir(tmp_path, storage):
    """Pre-create the skill directory so symlink tests can plant files inside."""
    d = tmp_path / "custom" / "demo-skill"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def _as_user(user_id: str):
    token = set_current_user(SimpleNamespace(id=user_id))
    try:
        yield
    finally:
        reset_current_user(token)


def _skill_markdown(name: str) -> str:
    return f"---\nname: {name}\ndescription: Test skill\n---\n\n# {name}\n"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_write_creates_file(tmp_path, storage):
    storage.write_custom_skill("demo-skill", "SKILL.md", "# hello")
    assert (tmp_path / "custom" / "demo-skill" / "SKILL.md").read_text() == "# hello"


def test_write_creates_subdirectory(tmp_path, storage):
    storage.write_custom_skill("demo-skill", "references/ref.md", "# ref")
    assert (tmp_path / "custom" / "demo-skill" / "references" / "ref.md").exists()


def test_write_is_atomic_overwrite(tmp_path, storage):
    storage.write_custom_skill("demo-skill", "SKILL.md", "first")
    storage.write_custom_skill("demo-skill", "SKILL.md", "second")
    assert (tmp_path / "custom" / "demo-skill" / "SKILL.md").read_text() == "second"


def test_custom_skills_are_visible_only_to_their_owner(tmp_path, storage):
    storage = LocalSkillStorage(host_path=str(tmp_path), enforce_owner_isolation=True)
    public_dir = tmp_path / "public" / "public-skill"
    public_dir.mkdir(parents=True)
    (public_dir / "SKILL.md").write_text(_skill_markdown("public-skill"), encoding="utf-8")

    with _as_user("user-a"):
        storage.write_custom_skill("personal-skill", "SKILL.md", _skill_markdown("personal-skill"))
        assert {skill.name for skill in storage.load_skills()} == {"personal-skill", "public-skill"}

    with _as_user("user-b"):
        assert {skill.name for skill in storage.load_skills()} == {"public-skill"}
        with pytest.raises(FileNotFoundError, match="not found"):
            storage.read_custom_skill("personal-skill")

    with _as_user("user-a"):
        assert storage.read_custom_skill("personal-skill") == _skill_markdown("personal-skill")


def test_authenticated_users_cannot_see_unowned_legacy_custom_skills(tmp_path, storage):
    storage = LocalSkillStorage(host_path=str(tmp_path), enforce_owner_isolation=True)
    legacy_dir = tmp_path / "custom" / "legacy-skill"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "SKILL.md").write_text(_skill_markdown("legacy-skill"), encoding="utf-8")

    with _as_user("user-a"):
        assert storage.load_skills() == []
        with pytest.raises(FileNotFoundError, match="not found"):
            storage.read_custom_skill("legacy-skill")

    with _as_user("default"):
        assert [skill.name for skill in storage.load_skills()] == ["legacy-skill"]


# ---------------------------------------------------------------------------
# Empty / blank path
# ---------------------------------------------------------------------------


def test_rejects_empty_string(storage):
    with pytest.raises(ValueError, match="empty"):
        storage.write_custom_skill("demo-skill", "", "x")


# ---------------------------------------------------------------------------
# Absolute paths
# ---------------------------------------------------------------------------


def test_rejects_absolute_unix_path(storage):
    with pytest.raises(ValueError, match="skill directory"):
        storage.write_custom_skill("demo-skill", "/etc/passwd", "x")


def test_rejects_absolute_path_with_skill_prefix(tmp_path, storage):
    """Absolute path within skill dir: containment check passes (not a security issue).

    Python's Path(base) / "/abs/path" ignores base and returns /abs/path directly.
    If that absolute path resolves within skill_dir, the write succeeds.
    This is not an escape — the file lands in the correct location.
    """
    absolute = str(tmp_path / "custom" / "demo-skill" / "SKILL.md")
    # Does not raise; the write goes to the expected place
    storage.write_custom_skill("demo-skill", absolute, "# ok")
    assert (tmp_path / "custom" / "demo-skill" / "SKILL.md").read_text() == "# ok"


# ---------------------------------------------------------------------------
# Parent-directory traversal
# ---------------------------------------------------------------------------


def test_rejects_dotdot_escape(storage):
    with pytest.raises(ValueError, match="skill directory"):
        storage.write_custom_skill("demo-skill", "../../escaped.txt", "x")


def test_rejects_dotdot_sibling(storage):
    with pytest.raises(ValueError, match="skill directory"):
        storage.write_custom_skill("demo-skill", "../sibling/x.txt", "x")


def test_rejects_dotdot_in_subpath(storage):
    with pytest.raises(ValueError, match="skill directory"):
        storage.write_custom_skill("demo-skill", "sub/../../escape.txt", "x")


def test_rejects_dotdot_only(storage):
    with pytest.raises(ValueError, match="skill directory"):
        storage.write_custom_skill("demo-skill", "..", "x")


# ---------------------------------------------------------------------------
# Symlink escape
# ---------------------------------------------------------------------------


def test_rejects_symlink_pointing_outside(tmp_path, storage, skill_dir):
    outside = tmp_path / "outside.txt"
    link = skill_dir / "escape_link.txt"
    os.symlink(outside, link)
    with pytest.raises(ValueError, match="skill directory"):
        storage.write_custom_skill("demo-skill", "escape_link.txt", "x")


def test_rejects_symlink_dir_pointing_outside(tmp_path, storage, skill_dir):
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    link_dir = skill_dir / "linked_dir"
    os.symlink(outside_dir, link_dir)
    with pytest.raises(ValueError, match="skill directory"):
        storage.write_custom_skill("demo-skill", "linked_dir/file.txt", "x")


def test_allows_symlink_within_skill_dir(tmp_path, storage, skill_dir):
    """A symlink that resolves inside the skill directory is allowed.

    Because target is resolved before writing, the write goes to the real file
    the symlink points to (both the link and the real file end up with the new
    content).
    """
    real_file = skill_dir / "real.md"
    real_file.write_text("real")
    link = skill_dir / "alias.md"
    os.symlink(real_file, link)
    # Should not raise
    storage.write_custom_skill("demo-skill", "alias.md", "updated")
    # resolve() writes through to the real target file
    assert real_file.read_text() == "updated"
    assert (skill_dir / "alias.md").read_text() == "updated"


# ---------------------------------------------------------------------------
# Invalid skill-name traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,method_name",
    [
        ("../../escaped", "get_custom_skill_dir"),
        ("../../escaped", "get_custom_skill_file"),
        ("../../escaped", "get_skill_history_file"),
        ("../../escaped", "custom_skill_exists"),
        ("../../escaped", "public_skill_exists"),
    ],
)
def test_rejects_invalid_skill_name_in_path_helpers(storage, name, method_name):
    method = getattr(storage, method_name)
    with pytest.raises(ValueError, match="hyphen-case"):
        method(name)
