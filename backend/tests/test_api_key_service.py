import hashlib
import hmac

import pytest

from app.gateway.external.config import ExternalAPIConfig
from app.gateway.external.service import APIKeyService


class FakeAPIKeyRepository:
    def __init__(self):
        self.rows = {}
        self.current = {}

    async def rotate(self, values):
        old = self.current.get(values["user_id"])
        if old:
            self.rows[old]["status"] = "revoked"
        row = {**values, "status": "active"}
        self.rows[values["id"]] = row
        self.current[values["user_id"]] = values["id"]
        return row

    async def get_active_by_id(self, key_id):
        row = self.rows.get(key_id)
        return row if row and row["status"] == "active" else None

    async def touch_last_used(self, key_id):
        self.rows[key_id]["touched"] = True

    async def revoke(self, user_id, reason="revoked"):
        key_id = self.current.get(user_id)
        if not key_id or self.rows[key_id]["status"] == "revoked":
            return False
        self.rows[key_id]["status"] = "revoked"
        return True

    async def update_policy(self, user_id, allowed_skills):
        key_id = self.current.get(user_id)
        if not key_id:
            return None
        self.rows[key_id]["allowed_skills"] = allowed_skills
        return self.rows[key_id]


@pytest.fixture
def service():
    return APIKeyService(FakeAPIKeyRepository(), ExternalAPIConfig(api_key_pepper="p" * 32))


@pytest.mark.anyio
async def test_rotate_returns_well_formed_key_and_persists_only_hash(service):
    result = await service.rotate(user_id="alice", allowed_skills=["sales-report", "sales-report"])
    key_id, secret = service.parse(result["api_key"])
    assert len(key_id) == 32
    assert len(secret) >= 40
    stored = service._repo.rows[key_id]
    assert stored["secret_hash"] == hmac.new(b"p" * 32, secret.encode(), hashlib.sha256).hexdigest()
    assert "api_key" not in stored and "secret" not in stored
    assert stored["allowed_skills"] == ["sales-report"]


@pytest.mark.anyio
async def test_authenticate_and_rotate_revoke_old_key(service):
    first = await service.rotate(user_id="alice")
    assert (await service.authenticate(first["api_key"]))["user_id"] == "alice"
    second = await service.rotate(user_id="alice")
    assert await service.authenticate(first["api_key"]) is None
    assert (await service.authenticate(second["api_key"]))["user_id"] == "alice"


def test_parse_rejects_malformed_key_without_repository_lookup(service):
    with pytest.raises(ValueError):
        service.parse("not-a-key")


@pytest.mark.anyio
async def test_policy_normalizes_and_revoke_is_idempotent(service):
    await service.rotate(user_id="alice")
    updated = await service.update_policy("alice", ["z-skill", "a-skill", "z-skill"])
    assert updated["allowed_skills"] == ["a-skill", "z-skill"]
    assert await service.revoke("alice") is True
    assert await service.revoke("alice") is False
