from __future__ import annotations

import logging
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from deerflow.publishing.secret_store import LocalEncryptedSecretStore, SecretStoreConfigurationError


@pytest.mark.asyncio
async def test_secret_store_round_trip_persists_only_ciphertext(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = LocalEncryptedSecretStore(tmp_path, key=Fernet.generate_key())
    plaintext = "fs-secret-do-not-leak"

    with caplog.at_level(logging.DEBUG):
        secret_ref = await store.put(plaintext)
        restored = await store.get(secret_ref)

    assert secret_ref.startswith("secret://feishu/")
    assert restored == plaintext
    assert plaintext not in caplog.text
    persisted = [path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()]
    assert len(persisted) == 1
    assert plaintext.encode() not in persisted[0]


@pytest.mark.asyncio
async def test_secret_store_rejects_unknown_or_malformed_refs(tmp_path: Path) -> None:
    store = LocalEncryptedSecretStore(tmp_path, key=Fernet.generate_key())

    with pytest.raises(KeyError):
        await store.get("secret://feishu/00000000000000000000000000000000")
    with pytest.raises(KeyError):
        await store.get("secret://feishu/../../config.yaml")


@pytest.mark.asyncio
async def test_secret_store_delete_makes_secret_unrecoverable(tmp_path: Path) -> None:
    store = LocalEncryptedSecretStore(tmp_path, key=Fernet.generate_key())
    secret_ref = await store.put("rotate-me")

    assert await store.delete(secret_ref) is True
    assert await store.delete(secret_ref) is False
    with pytest.raises(KeyError):
        await store.get(secret_ref)


@pytest.mark.asyncio
async def test_pending_secret_ownership_survives_until_acknowledged(tmp_path: Path) -> None:
    store = LocalEncryptedSecretStore(tmp_path, key=Fernet.generate_key())

    secret_ref = await store.put_pending(
        "candidate",
        agent_id="pa_1",
        binding_id="binding-1",
        owner_user_id="owner-a",
    )

    records = await store.list_pending()
    assert [(record.secret_ref, record.binding_id) for record in records] == [(secret_ref, "binding-1")]
    assert await store.get(secret_ref) == "candidate"
    assert await store.acknowledge_pending(secret_ref) is True
    assert await store.list_pending() == []


def test_secret_store_requires_a_valid_fernet_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEER_FLOW_SECRET_STORE_KEY", raising=False)

    with pytest.raises(SecretStoreConfigurationError):
        LocalEncryptedSecretStore(tmp_path)
    with pytest.raises(SecretStoreConfigurationError):
        LocalEncryptedSecretStore(tmp_path, key="not-a-fernet-key")
