from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.fernet import Fernet

from deerflow.connectors.errors import ConnectorSecretError
from deerflow.connectors.schemas import ConnectorCredentialRef, ConnectorRuntimeContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecretValue:
    value: str


class SecretStore(Protocol):
    def get_secret(self, credential: ConnectorCredentialRef, context: ConnectorRuntimeContext | None = None) -> SecretValue:
        ...


class EnvSecretStore:
    def get_secret(self, credential: ConnectorCredentialRef, context: ConnectorRuntimeContext | None = None) -> SecretValue:  # noqa: ARG002
        if credential.provider != "env":
            raise ConnectorSecretError(f"Unsupported secret provider for EnvSecretStore: {credential.provider}")
        value = os.getenv(credential.ref or "")
        if value is None:
            raise ConnectorSecretError(f"Connector secret environment variable is not set: {credential.ref}", recoverable=True)
        return SecretValue(value=value)


class InlineSecretStore:
    """Encrypt/decrypt inline credentials stored in the connector record.

    The encryption key is read from ``DEERFLOW_CONNECTOR_KEY`` env var.
    If missing, a development-only fixed key is used with a loud warning.
    """

    def __init__(self) -> None:
        self._fernet = self._load_fernet()

    @staticmethod
    def _load_fernet() -> Fernet:
        key = os.getenv("DEERFLOW_CONNECTOR_KEY")
        if key:
            return Fernet(key.encode())
        logger.warning(
            "DEERFLOW_CONNECTOR_KEY is not set. Using a development-only fixed encryption key. "
            "Set DEERFLOW_CONNECTOR_KEY to a Fernet key in production!"
        )
        # A fixed 32-byte base64-encoded key for dev only.
        return Fernet(b"4V7-x8I_l1G6a9Zp3KQmR2T5NwUeY0DcHjBvFqOsEtg=")

    def encrypt(self, payload: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(payload).encode()).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        return json.loads(self._fernet.decrypt(token.encode()).decode())

    def get_secret(self, credential: ConnectorCredentialRef, context: ConnectorRuntimeContext | None = None) -> SecretValue:  # noqa: ARG002
        if credential.provider != "inline":
            raise ConnectorSecretError(f"Unsupported secret provider for InlineSecretStore: {credential.provider}")

        # Fresh credential supplied directly (create / test flow).
        if credential.password:
            return SecretValue(
                value=json.dumps({"username": credential.username or "", "password": credential.password})
            )

        # Persisted credential: ref holds the encrypted token.
        if credential.ref:
            try:
                payload = self.decrypt(credential.ref)
                # The username in the encrypted blob reflects the moment the
                # secret was last rotated; if the caller has since updated
                # the account name (and supplied a non-null username here)
                # we honor that newer value so the runtime connects as the
                # user the operator is currently looking at.
                if credential.username:
                    payload = {**payload, "username": credential.username}
                return SecretValue(value=json.dumps(payload))
            except Exception as exc:
                raise ConnectorSecretError("Failed to decrypt inline credential", recoverable=True) from exc

        raise ConnectorSecretError("Inline credential requires a password or encrypted ref", recoverable=True)


class MultiSecretStore:
    """Dispatch to the right backend based on ``credential.provider``.

    The default connector service is created without an explicit store; this
    combined store lets the service transparently support the env-based
    references (for ops-managed secrets) and the inline (encrypted) form (for
    per-tenant credentials stored in the connector record itself).
    """

    def __init__(self, env_store: EnvSecretStore | None = None, inline_store: InlineSecretStore | None = None) -> None:
        self._env = env_store or EnvSecretStore()
        self._inline = inline_store or InlineSecretStore()

    def get_secret(self, credential: ConnectorCredentialRef, context: ConnectorRuntimeContext | None = None) -> SecretValue:
        provider = credential.provider
        if provider == "env":
            return self._env.get_secret(credential, context)
        if provider == "inline":
            return self._inline.get_secret(credential, context)
        raise ConnectorSecretError(f"Unsupported secret provider: {provider}", recoverable=True)


_URL_SECRET_RE = re.compile(r"(?P<prefix>://[^:/\s]+:)(?P<secret>[^@\s]+)(?P<suffix>@)")


def redact_secret_text(text: str | None) -> str:
    if not text:
        return ""
    text = _URL_SECRET_RE.sub(r"\g<prefix>***\g<suffix>", text)
    for key in ("password", "passwd", "token", "secret", "api_key"):
        text = re.sub(rf"({key}\s*[=:]\s*)[^,\s;]+", r"\1***", text, flags=re.IGNORECASE)
    return text
