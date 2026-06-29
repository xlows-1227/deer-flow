from __future__ import annotations

import json
import logging
import os

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class ModelSecretStore:
    """Encrypt/decrypt user model API keys stored in the database."""

    def __init__(self) -> None:
        self._fernet = self._load_fernet()

    @staticmethod
    def _load_fernet() -> Fernet:
        key = os.getenv("DEERFLOW_MODEL_KEY")
        if key:
            return Fernet(key.encode())
        logger.warning("DEERFLOW_MODEL_KEY is not set. Using a development-only fixed encryption key. Set DEERFLOW_MODEL_KEY to a Fernet key in production!")
        return Fernet(b"4V7-x8I_l1G6a9Zp3KQmR2T5NwUeY0DcHjBvFqOsEtg=")

    def encrypt_api_key(self, api_key: str) -> str:
        return self._fernet.encrypt(json.dumps({"api_key": api_key}).encode()).decode()

    def decrypt_api_key(self, token: str) -> str:
        payload = json.loads(self._fernet.decrypt(token.encode()).decode())
        if not isinstance(payload, dict):
            raise ValueError("Invalid encrypted API key payload")
        api_key = payload.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Encrypted API key payload is missing api_key")
        return api_key
