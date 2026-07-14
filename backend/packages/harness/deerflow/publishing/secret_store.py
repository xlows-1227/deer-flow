"""Encrypted secret storage for Published-Agent integrations.

The database only persists opaque ``secret_ref`` values. Ciphertext is kept in
this store and encrypted with a deployment key supplied through
``DEER_FLOW_SECRET_STORE_KEY``. Secret values are never included in refs,
exceptions, or logs.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

_SECRET_STORE_KEY_ENV = "DEER_FLOW_SECRET_STORE_KEY"
_REF_PATTERN = re.compile(r"^secret://feishu/(?P<secret_id>[0-9a-f]{32})$")


class SecretStoreConfigurationError(RuntimeError):
    """Raised when the deployment encryption key is absent or invalid."""


class SecretStoreIntegrityError(RuntimeError):
    """Raised when persisted ciphertext cannot be authenticated."""


class SecretStore(Protocol):
    """Asynchronous opaque-reference storage for integration secrets."""

    async def put(self, secret: str) -> str:
        """Encrypt ``secret`` and return an opaque reference."""

    async def get(self, secret_ref: str) -> str:
        """Resolve and decrypt one secret reference."""

    async def delete(self, secret_ref: str) -> bool:
        """Delete one secret, returning whether it existed."""


class LocalEncryptedSecretStore:
    """Fernet-encrypted local filesystem implementation of :class:`SecretStore`."""

    def __init__(self, base_dir: str | Path, *, key: str | bytes | None = None) -> None:
        self._base_dir = Path(base_dir)
        raw_key = key if key is not None else os.environ.get(_SECRET_STORE_KEY_ENV)
        if raw_key is None:
            raise SecretStoreConfigurationError(f"{_SECRET_STORE_KEY_ENV} is required")
        try:
            if isinstance(raw_key, str):
                raw_key = raw_key.encode("ascii", errors="strict")
            self._fernet = Fernet(raw_key)
        except (TypeError, UnicodeEncodeError, ValueError) as exc:
            raise SecretStoreConfigurationError(f"{_SECRET_STORE_KEY_ENV} must be a valid Fernet key") from exc

    def _path_for_ref(self, secret_ref: str) -> Path:
        match = _REF_PATTERN.fullmatch(secret_ref)
        if match is None:
            raise KeyError("unknown secret reference")
        secret_id = match.group("secret_id")
        return self._base_dir / secret_id[:2] / f"{secret_id}.secret"

    async def put(self, secret: str) -> str:
        if not isinstance(secret, str) or not secret:
            raise ValueError("secret must be a non-empty string")
        secret_id = uuid4().hex
        secret_ref = f"secret://feishu/{secret_id}"
        target = self._path_for_ref(secret_ref)
        ciphertext = self._fernet.encrypt(secret.encode("utf-8"))

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(dir=target.parent, prefix=".secret-", suffix=".tmp", delete=False)
            temp_path = Path(handle.name)
            try:
                handle.write(ciphertext)
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
                try:
                    temp_path.chmod(0o600)
                except OSError:
                    pass
                temp_path.replace(target)
            except BaseException:
                handle.close()
                temp_path.unlink(missing_ok=True)
                raise

        await asyncio.to_thread(_write)
        return secret_ref

    async def get(self, secret_ref: str) -> str:
        path = self._path_for_ref(secret_ref)

        def _read() -> bytes:
            try:
                return path.read_bytes()
            except FileNotFoundError as exc:
                raise KeyError("unknown secret reference") from exc

        ciphertext = await asyncio.to_thread(_read)
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise SecretStoreIntegrityError("secret ciphertext failed authentication") from exc
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretStoreIntegrityError("secret plaintext is not valid UTF-8") from exc

    async def delete(self, secret_ref: str) -> bool:
        path = self._path_for_ref(secret_ref)

        def _delete() -> bool:
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return True

        return await asyncio.to_thread(_delete)


def get_secret_store(*, base_dir: str | Path | None = None) -> LocalEncryptedSecretStore:
    """Build the process secret store from the deployment environment."""
    if base_dir is None:
        from deerflow.config.paths import get_paths

        base_dir = Path(get_paths().base_dir) / "secret-store" / "feishu"
    return LocalEncryptedSecretStore(base_dir)


__all__ = [
    "LocalEncryptedSecretStore",
    "SecretStore",
    "SecretStoreConfigurationError",
    "SecretStoreIntegrityError",
    "get_secret_store",
]
