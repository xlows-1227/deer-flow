"""Encrypted secret storage for Published-Agent integrations.

The database only persists opaque ``secret_ref`` values. Ciphertext is kept in
this store and encrypted with a deployment key supplied through
``DEER_FLOW_SECRET_STORE_KEY``. Secret values are never included in refs,
exceptions, or logs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

_SECRET_STORE_KEY_ENV = "DEER_FLOW_SECRET_STORE_KEY"
_REF_PATTERN = re.compile(r"^secret://feishu/(?P<secret_id>[0-9a-f]{32})$")
SECRET_PENDING_INGEST_GRACE_SECONDS = 120.0


@dataclass(frozen=True)
class PendingSecretRecord:
    """Durable ownership for ciphertext created before its binding transaction."""

    secret_ref: str
    agent_id: str
    binding_id: str
    owner_user_id: str
    not_before: float


class SecretStoreConfigurationError(RuntimeError):
    """Raised when the deployment encryption key is absent or invalid."""


class SecretStoreIntegrityError(RuntimeError):
    """Raised when persisted ciphertext cannot be authenticated."""


class SecretStore(Protocol):
    """Asynchronous opaque-reference storage for integration secrets."""

    async def put(self, secret: str) -> str:
        """Encrypt ``secret`` and return an opaque reference."""

    def new_ref(self) -> str:
        """Allocate an opaque reference before database ownership is recorded."""

    async def put_reserved(self, secret_ref: str, secret: str) -> None:
        """Encrypt ``secret`` at a reference already owned by persistence."""

    async def put_pending(
        self,
        secret: str,
        *,
        agent_id: str,
        binding_id: str,
        owner_user_id: str,
    ) -> str:
        """Encrypt a secret with durable pre-database ownership."""

    async def get(self, secret_ref: str) -> str:
        """Resolve and decrypt one secret reference."""

    async def delete(self, secret_ref: str) -> bool:
        """Delete one secret, returning whether it existed."""

    async def acknowledge_pending(self, secret_ref: str) -> bool:
        """Acknowledge that a durable database owner now exists or erase completed."""

    async def list_pending(self) -> list[PendingSecretRecord]:
        """List durable pre-database ownership records for recovery."""


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

    def _pending_path_for_ref(self, secret_ref: str) -> Path:
        match = _REF_PATTERN.fullmatch(secret_ref)
        if match is None:
            raise KeyError("unknown secret reference")
        return self._base_dir / ".pending" / f"{match.group('secret_id')}.json"

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}-", suffix=".tmp", delete=False)
        temp_path = Path(handle.name)
        try:
            handle.write(content)
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

    async def put(self, secret: str) -> str:
        secret_ref = self.new_ref()
        await self._put(secret_ref, secret, pending=None)
        return secret_ref

    def new_ref(self) -> str:
        """Return a random opaque reference without writing ciphertext."""
        return f"secret://feishu/{uuid4().hex}"

    async def put_reserved(self, secret_ref: str, secret: str) -> None:
        """Write ciphertext only after the caller records durable DB ownership."""
        self._path_for_ref(secret_ref)
        await self._put(secret_ref, secret, pending=None)

    async def put_pending(
        self,
        secret: str,
        *,
        agent_id: str,
        binding_id: str,
        owner_user_id: str,
    ) -> str:
        """Write pending ownership before ciphertext so a crash cannot orphan the ref."""
        secret_ref = self.new_ref()
        await self._put(
            secret_ref,
            secret,
            pending={
                "agent_id": agent_id,
                "binding_id": binding_id,
                "owner_user_id": owner_user_id,
                "not_before": time.time() + SECRET_PENDING_INGEST_GRACE_SECONDS,
            },
        )
        return secret_ref

    async def _put(self, secret_ref: str, secret: str, *, pending: dict[str, object] | None) -> None:
        if not isinstance(secret, str) or not secret:
            raise ValueError("secret must be a non-empty string")
        target = self._path_for_ref(secret_ref)
        ciphertext = self._fernet.encrypt(secret.encode("utf-8"))

        def _write() -> None:
            pending_path = self._pending_path_for_ref(secret_ref)
            try:
                if pending is not None:
                    self._atomic_write(
                        pending_path,
                        json.dumps({"secret_ref": secret_ref, **pending}, sort_keys=True).encode("utf-8"),
                    )
                self._atomic_write(target, ciphertext)
            except BaseException:
                target.unlink(missing_ok=True)
                pending_path.unlink(missing_ok=True)
                raise

        await asyncio.to_thread(_write)

    async def acknowledge_pending(self, secret_ref: str) -> bool:
        path = self._pending_path_for_ref(secret_ref)

        def _acknowledge() -> bool:
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return True

        return await asyncio.to_thread(_acknowledge)

    async def list_pending(self) -> list[PendingSecretRecord]:
        pending_dir = self._base_dir / ".pending"

        def _list() -> list[PendingSecretRecord]:
            if not pending_dir.exists():
                return []
            records: list[PendingSecretRecord] = []
            for path in pending_dir.glob("*.json"):
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise SecretStoreIntegrityError("pending secret ownership is not an object")
                try:
                    record = PendingSecretRecord(
                        secret_ref=str(payload["secret_ref"]),
                        agent_id=str(payload["agent_id"]),
                        binding_id=str(payload["binding_id"]),
                        owner_user_id=str(payload["owner_user_id"]),
                        not_before=float(payload["not_before"]),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise SecretStoreIntegrityError("pending secret ownership is invalid") from exc
                if self._pending_path_for_ref(record.secret_ref) != path:
                    raise SecretStoreIntegrityError("pending secret ownership ref does not match its filename")
                records.append(record)
            return records

        return await asyncio.to_thread(_list)

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
    "PendingSecretRecord",
    "SECRET_PENDING_INGEST_GRACE_SECONDS",
    "SecretStore",
    "SecretStoreConfigurationError",
    "SecretStoreIntegrityError",
    "get_secret_store",
]
