from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet

from deerflow.publishing.secret_store import LocalEncryptedSecretStore, SecretStoreConfigurationError


@pytest.mark.asyncio
async def test_secret_store_round_trip_persists_only_ciphertext(tmp_path, caplog) -> None:
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
async def test_secret_store_rejects_unknown_or_malformed_refs(tmp_path) -> None:
    store = LocalEncryptedSecretStore(tmp_path, key=Fernet.generate_key())

    with pytest.raises(KeyError):
        await store.get("secret://feishu/00000000000000000000000000000000")
    with pytest.raises(KeyError):
        await store.get("secret://feishu/../../config.yaml")


@pytest.mark.asyncio
async def test_secret_store_delete_makes_secret_unrecoverable(tmp_path) -> None:
    store = LocalEncryptedSecretStore(tmp_path, key=Fernet.generate_key())
    secret_ref = await store.put("rotate-me")

    assert await store.delete(secret_ref) is True
    assert await store.delete(secret_ref) is False
    with pytest.raises(KeyError):
        await store.get(secret_ref)


def test_secret_store_requires_a_valid_fernet_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEER_FLOW_SECRET_STORE_KEY", raising=False)

    with pytest.raises(SecretStoreConfigurationError):
        LocalEncryptedSecretStore(tmp_path)
    with pytest.raises(SecretStoreConfigurationError):
        LocalEncryptedSecretStore(tmp_path, key="not-a-fernet-key")
