"""Application services shared by External API routers."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime
from typing import Any

from app.gateway.external.config import ExternalAPIConfig, get_external_api_config
from app.gateway.external.models import validate_external_name
from deerflow.persistence.api_key import APIKeyRepository
from deerflow.persistence.external_conversation import ExternalConversationRepository

DEFAULT_EXTERNAL_SCOPES = [
    "external:conversations:create",
    "external:conversations:read",
    "external:conversations:write",
    "external:runs:create",
    "external:runs:read",
    "external:runs:cancel",
    "external:skills:read",
]

_KEY_RE = re.compile(r"^dfk_([0-9a-f]{32})_([A-Za-z0-9_-]{40,})$")


class APIKeyService:
    def __init__(self, repository: APIKeyRepository, config: ExternalAPIConfig | None = None) -> None:
        self._repo = repository
        self._config = config or get_external_api_config()

    @staticmethod
    def parse(api_key: str) -> tuple[str, str]:
        match = _KEY_RE.fullmatch(api_key)
        if match is None:
            raise ValueError("Malformed External API Key")
        return match.group(1), match.group(2)

    def hash_secret(self, secret: str) -> str:
        return hmac.new(self._config.api_key_pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()

    def verify_secret(self, secret: str, expected_hash: str) -> bool:
        return hmac.compare_digest(self.hash_secret(secret), expected_hash)

    async def rotate(
        self,
        *,
        user_id: str,
        allowed_skills: list[str] | None = None,
        name: str = "Default external API key",
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        key_id = secrets.token_hex(16)
        secret = secrets.token_urlsafe(32)
        api_key = f"dfk_{key_id}_{secret}"
        stored = await self._repo.rotate(
            {
                "id": key_id,
                "user_id": user_id,
                "name": name,
                "secret_hash": self.hash_secret(secret),
                "key_prefix": f"dfk_{key_id[:8]}",
                "last_four": secret[-4:],
                "scopes": DEFAULT_EXTERNAL_SCOPES,
                "allowed_skills": self.normalize_allowed_skills(allowed_skills or []),
                "expires_at": expires_at,
            }
        )
        return {**stored, "api_key": api_key}

    async def authenticate(self, api_key: str) -> dict[str, Any] | None:
        key_id, secret = self.parse(api_key)
        stored = await self._repo.get_active_by_id(key_id)
        if stored is None or not self.verify_secret(secret, stored["secret_hash"]):
            return None
        await self._repo.touch_last_used(key_id)
        return stored

    async def revoke(self, user_id: str) -> bool:
        return await self._repo.revoke(user_id)

    async def update_policy(self, user_id: str, allowed_skills: list[str]) -> dict[str, Any] | None:
        return await self._repo.update_policy(user_id, self.normalize_allowed_skills(allowed_skills))

    @staticmethod
    def normalize_allowed_skills(skills: list[str]) -> list[str]:
        return sorted({validate_external_name(skill, field_name="skill") for skill in skills})


class ExternalConversationService:
    def __init__(self, repository: ExternalConversationRepository, *, thread_store, checkpointer) -> None:
        self._repo = repository
        self._thread_store = thread_store
        self._checkpointer = checkpointer

    async def create(
        self,
        *,
        user_id: str,
        source: str,
        external_conversation_id: str | None,
        agent_id: str,
        default_skill_name: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        from uuid import uuid4

        from app.gateway.thread_service import create_empty_thread

        conversation_id = f"conv_{uuid4().hex}"
        thread_id = str(uuid4())
        thread_metadata = {
            "external_api": True,
            "external_user_id": user_id,
            "external_conversation_id": conversation_id,
            "external_source": source,
            "client_metadata": metadata,
        }
        await create_empty_thread(
            thread_store=self._thread_store,
            checkpointer=self._checkpointer,
            assistant_id=agent_id,
            metadata=thread_metadata,
            thread_id=thread_id,
        )
        try:
            return await self._repo.create(
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "source": source,
                    "external_conversation_id": external_conversation_id,
                    "thread_id": thread_id,
                    "agent_id": agent_id,
                    "default_skill_name": default_skill_name,
                }
            )
        except Exception:
            await self._thread_store.delete(thread_id)
            if hasattr(self._checkpointer, "adelete_thread"):
                await self._checkpointer.adelete_thread(thread_id)
            raise
