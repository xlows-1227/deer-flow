"""Tests for the production publishing adapters (rereview Important-3 / Important-4).

Covers the availability guards added to ``StorageSkillsIndex`` (disabled skills
are not selectable) and ``ConnectorServiceRepo`` (disabled/deleted connectors
are not grantable), plus the import factory index ``get()`` (Important-4).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from deerflow.publishing.skills_index import ConnectorServiceRepo, StorageSkillsIndex
from deerflow.skills.types import SkillCategory


def _make_storage(skills: list[dict[str, Any]], owners: dict[str, str] | None = None):
    """Build a fake SkillStorage exposing load_skills + _read_custom_skill_owner."""
    owners = owners or {}

    class _FakeStorage:
        def load_skills(self, *, enabled_only=False):
            out = []
            for s in skills:
                if enabled_only and not s.get("enabled"):
                    continue
                out.append(
                    SimpleNamespace(
                        name=s["name"],
                        category=SkillCategory(s["category"]),
                        enabled=s.get("enabled", True),
                        skill_dir=s.get("skill_dir"),
                        connector_requirements=s.get("connector_requirements") or [],
                    )
                )
            return out

        def _read_custom_skill_owner(self, skill_dir):  # noqa: ARG002
            # Match by skill_dir to find the skill name, then look up the owner.
            for s in skills:
                if s.get("skill_dir") == skill_dir and s["name"] in owners:
                    return owners[s["name"]]
            return None

    storage = _FakeStorage()
    return storage


def test_disabled_public_skill_is_not_selectable():
    storage = _make_storage([{"name": "reporting", "category": "public", "enabled": False}])
    index = StorageSkillsIndex(storage, owner_user_id="user-a")
    assert index.is_selectable_by("reporting", "user-a") is False


def test_enabled_public_skill_is_selectable():
    storage = _make_storage([{"name": "reporting", "category": "public", "enabled": True}])
    index = StorageSkillsIndex(storage, owner_user_id="user-a")
    assert index.is_selectable_by("reporting", "user-a") is True


def test_disabled_private_skill_is_not_selectable_even_for_owner():
    storage = _make_storage(
        [{"name": "private-x", "category": "custom", "enabled": False, "skill_dir": "/x"}],
        owners={"private-x": "user-a"},
    )
    index = StorageSkillsIndex(storage, owner_user_id="user-a")
    assert index.is_selectable_by("private-x", "user-a") is False


@pytest.mark.anyio
async def test_disabled_connector_is_not_grantable():
    class _FakeService:
        async def get_connector(self, connector_id, *, owner_id=...):
            return SimpleNamespace(model_dump=lambda: {"id": connector_id, "owner_id": owner_id, "status": "disabled"})

    repo = ConnectorServiceRepo(_FakeService())
    assert await repo.get_instance("conn_1", owner_id="user-a") is None


@pytest.mark.anyio
async def test_pending_or_error_connector_is_not_grantable():
    """Third-review Important-3: only status == 'active' is grantable."""
    for status in ("pending", "error", "unknown", ""):
        cls = type(
            "_Fake",
            (),
            {
                "get_connector": lambda self, connector_id, owner_id=..., _s=status: SimpleNamespace(
                    model_dump=lambda _cid=connector_id, _oid=owner_id, _s=_s: {
                        "id": _cid,
                        "owner_id": _oid,
                        "status": _s,
                    }
                )
            },
        )
        repo = ConnectorServiceRepo(cls())
        assert await repo.get_instance("conn_1", owner_id="user-a") is None, f"status={status!r} should be rejected"


@pytest.mark.anyio
async def test_active_connector_is_grantable(monkeypatch):
    """An active connector of an enabled type is grantable."""

    class _ConnCfg:
        enabled = True
        enabled_types: list = []

    class _Cfg:
        connectors = _ConnCfg()

    import deerflow.config.app_config as app_cfg_mod

    monkeypatch.setattr(app_cfg_mod, "get_app_config", lambda: _Cfg())

    class _FakeService:
        async def get_connector(self, connector_id, *, owner_id=...):
            return SimpleNamespace(model_dump=lambda: {"id": connector_id, "owner_id": owner_id, "status": "active", "type": "mysql"})

        async def get_connector_type(self, type_name):
            if type_name == "mysql":
                return {"type": "mysql"}
            raise KeyError(f"unknown type: {type_name}")

    repo = ConnectorServiceRepo(_FakeService())
    result = await repo.get_instance("conn_1", owner_id="user-a")
    assert result is not None
    assert result["status"] == "active"


@pytest.mark.anyio
async def test_unknown_type_rejected_even_with_empty_whitelist(monkeypatch):
    """Fifth-review Important-2: an active instance of an unregistered type must
    be rejected even when enabled_types is empty (unrestricted)."""

    class _ConnCfg:
        enabled = True
        enabled_types: list = []

    class _Cfg:
        connectors = _ConnCfg()

    import deerflow.config.app_config as app_cfg_mod

    monkeypatch.setattr(app_cfg_mod, "get_app_config", lambda: _Cfg())

    class _FakeService:
        async def get_connector(self, connector_id, *, owner_id=...):
            return SimpleNamespace(model_dump=lambda: {"id": connector_id, "status": "active", "type": "bogus"})

        async def get_connector_type(self, type_name):
            raise KeyError(f"unknown type: {type_name}")

    repo = ConnectorServiceRepo(_FakeService())
    assert await repo.get_instance("conn_1", owner_id="user-a") is None


@pytest.mark.anyio
async def test_deleted_connector_is_not_grantable():
    class _FakeService:
        async def get_connector(self, connector_id, *, owner_id=...):
            return SimpleNamespace(model_dump=lambda: {"id": connector_id, "owner_id": owner_id, "status": "deleted"})

    repo = ConnectorServiceRepo(_FakeService())
    assert await repo.get_instance("conn_1", owner_id="user-a") is None


@pytest.mark.anyio
async def test_connector_rejected_when_platform_disabled(monkeypatch):
    """Fourth-review Important-2: when connectors.enabled is False, even an
    active instance must not be grantable."""

    class _ConnCfg:
        enabled = False
        enabled_types: list = []

    class _Cfg:
        connectors = _ConnCfg()

    import deerflow.config.app_config as app_cfg_mod

    monkeypatch.setattr(app_cfg_mod, "get_app_config", lambda: _Cfg())

    class _FakeService:
        async def get_connector(self, connector_id, *, owner_id=...):
            return SimpleNamespace(model_dump=lambda: {"id": connector_id, "owner_id": owner_id, "status": "active", "type": "mysql"})

    repo = ConnectorServiceRepo(_FakeService())
    assert await repo.get_instance("conn_1", owner_id="user-a") is None


@pytest.mark.anyio
async def test_connector_rejected_on_config_failure(monkeypatch):
    """Fourth-review Important-2: a config read exception must fail closed."""
    import deerflow.config.app_config as app_cfg_mod

    def _boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(app_cfg_mod, "get_app_config", _boom)

    class _FakeService:
        async def get_connector(self, connector_id, *, owner_id=...):
            return SimpleNamespace(model_dump=lambda: {"id": connector_id, "owner_id": owner_id, "status": "active", "type": "mysql"})

    repo = ConnectorServiceRepo(_FakeService())
    assert await repo.get_instance("conn_1", owner_id="user-a") is None


@pytest.mark.anyio
async def test_connector_rejected_when_type_not_in_whitelist(monkeypatch):
    """Fourth-review Important-2: active instance of a type not in the
    platform's enabled_types whitelist is rejected."""

    class _ConnCfg:
        enabled = True
        enabled_types = ["mysql"]

    class _Cfg:
        connectors = _ConnCfg()

    import deerflow.config.app_config as app_cfg_mod

    monkeypatch.setattr(app_cfg_mod, "get_app_config", lambda: _Cfg())

    class _FakeService:
        async def get_connector(self, connector_id, *, owner_id=...):
            return SimpleNamespace(model_dump=lambda: {"id": connector_id, "owner_id": owner_id, "status": "active", "type": "postgres"})

    repo = ConnectorServiceRepo(_FakeService())
    assert await repo.get_instance("conn_1", owner_id="user-a") is None


def test_storage_skills_index_exposes_get_for_public():
    """StorageSkillsIndex.get() returns metadata for a public skill."""
    storage = _make_storage([{"name": "reporting", "category": "public", "enabled": True}])
    index = StorageSkillsIndex(storage, owner_user_id="user-a")
    assert hasattr(index, "get")
    info = index.get("reporting")
    assert info is not None
    assert info["visibility"] == "public"


def test_storage_skills_index_reports_private_visibility():
    """A private (custom) skill owned by the current user is reported as
    visibility='private' with the correct owner."""
    storage = _make_storage(
        [{"name": "my-private", "category": "custom", "enabled": True, "skill_dir": "/p"}],
        owners={"my-private": "user-a"},
    )
    index = StorageSkillsIndex(storage, owner_user_id="user-a")
    info = index.get("my-private")
    assert info is not None, "private skill metadata should resolve"
    assert info["visibility"] == "private"
    assert info["owner"] == "user-a"


def test_storage_skill_publish_snapshot_is_fail_closed_and_immutable(tmp_path):
    skill_dir = tmp_path / "reporting"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# Captured", encoding="utf-8")
    storage = _make_storage(
        [
            {
                "name": "reporting",
                "category": "public",
                "enabled": True,
                "skill_dir": skill_dir,
            }
        ]
    )
    index = StorageSkillsIndex(storage, owner_user_id="user-a")

    snapshots = index.resolve_publish_snapshots(["reporting"], "user-a")
    snapshot = snapshots["reporting"]
    assert snapshot is not None
    skill_file.write_text("# Changed later", encoding="utf-8")
    assert snapshot.file_map()["SKILL.md"] == b"# Captured"


def test_storage_skill_publish_snapshot_rejects_missing_skill_md(tmp_path):
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "notes.txt").write_text("not a skill", encoding="utf-8")
    storage = _make_storage(
        [
            {
                "name": "broken",
                "category": "public",
                "enabled": True,
                "skill_dir": skill_dir,
            }
        ]
    )
    index = StorageSkillsIndex(storage, owner_user_id="user-a")
    assert index.resolve_publish_snapshots(["broken"], "user-a") == {"broken": None}


def test_storage_private_skill_publish_snapshot_requires_exact_owner(tmp_path):
    skill_dir = tmp_path / "private"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Private", encoding="utf-8")
    storage = _make_storage(
        [
            {
                "name": "private",
                "category": "custom",
                "enabled": True,
                "skill_dir": skill_dir,
            }
        ],
        owners={"private": "user-a"},
    )
    index = StorageSkillsIndex(storage, owner_user_id="user-a")
    own = index.resolve_publish_snapshots(["private"], "user-a")["private"]
    assert own is not None
    assert own.visibility == "private"
    assert own.owner_user_id == "user-a"
    assert index.resolve_publish_snapshots(["private"], "user-b") == {"private": None}


def test_build_import_service_uses_owner_aware_production_index(monkeypatch, tmp_path):
    """Eleventh-review Minor-4: exercise the factory adapter, not only its delegate."""
    import deerflow.config.paths as paths_module
    import deerflow.publishing.factory as factory
    import deerflow.skills.storage as storage_module

    storage = _make_storage(
        [
            {"name": "public-skill", "category": "public", "enabled": True},
            {"name": "private-skill", "category": "custom", "enabled": True, "skill_dir": "/private"},
        ],
        owners={"private-skill": "user-a"},
    )
    monkeypatch.setattr(factory, "get_session_factory", lambda: object())
    monkeypatch.setattr(storage_module, "get_or_new_skill_storage", lambda: storage)
    monkeypatch.setattr(paths_module, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))

    service = factory.build_import_service()
    assert service is not None
    index = service._skills  # noqa: SLF001 - verifies the production factory wiring
    assert index.is_selectable_by("public-skill", "user-b") is True
    assert index.is_selectable_by("private-skill", "user-a") is True
    assert index.is_selectable_by("private-skill", "user-b") is False
    assert index.get("private-skill")["visibility"] == "private"
