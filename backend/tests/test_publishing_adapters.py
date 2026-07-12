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
            return owners.get(s["name"]) if (s := {"name": ""}) else None

    storage = _FakeStorage()
    # Patch _read_custom_skill_owner to use the name→owner map by name lookup.
    storage._read_custom_skill_owner = lambda skill_dir=None, _owners=owners, _skills=skills: next(  # noqa: E731
        (_owners[sk["name"]] for sk in _skills if sk.get("skill_dir") == skill_dir and sk["name"] in _owners), None
    )
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

    repo = ConnectorServiceRepo(_FakeService())
    result = await repo.get_instance("conn_1", owner_id="user-a")
    assert result is not None
    assert result["status"] == "active"


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
    """Rereview Important-4: the production skills indexes must expose get().

    The draft/publish factory index (``_OwnerAwareSkillsIndex``) is module-level
    and used as the template for the import adapter; both now implement get()
    so visibility/ownership is derived authoritatively.
    """
    from deerflow.publishing.factory import _OwnerAwareSkillsIndex

    storage = _make_storage([{"name": "reporting", "category": "public", "enabled": True}])
    index = _OwnerAwareSkillsIndex(storage)
    assert hasattr(index, "get")
    info = index.get("reporting")
    assert info is not None
    assert info["visibility"] == "public"
