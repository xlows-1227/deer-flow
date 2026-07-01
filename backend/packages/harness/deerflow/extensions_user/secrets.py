from __future__ import annotations

from typing import Any

from deerflow.user_models.secrets import ModelSecretStore


class ExtensionSecretStore:
    """Encrypt/decrypt JSON secrets for per-user MCP and image settings."""

    def __init__(self, *, store: ModelSecretStore | None = None) -> None:
        self._store = store or ModelSecretStore()

    def encrypt_json(self, payload: dict[str, Any]) -> str:
        return self._store.encrypt_json(payload)

    def decrypt_json(self, token: str) -> dict[str, Any]:
        return self._store.decrypt_json(token)

    def encrypt_api_key(self, api_key: str) -> str:
        return self._store.encrypt_api_key(api_key)

    def decrypt_api_key(self, token: str) -> str:
        return self._store.decrypt_api_key(token)
