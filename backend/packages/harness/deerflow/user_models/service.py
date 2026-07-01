from __future__ import annotations

from typing import Any

from deerflow.config.effective_config import invalidate_user_model_cache
from deerflow.config.model_config import ModelConfig
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.user_model import UserModelRepository
from deerflow.user_models.schemas import (
    DEFAULT_BASE_URLS,
    MASKED_API_KEY,
    PROVIDER_USE_MAP,
    UserModelCreateRequest,
    UserModelRecord,
    UserModelUpdateRequest,
)
from deerflow.user_models.secrets import ModelSecretStore


class UserModelValidationError(ValueError):
    pass


class UserModelNotFoundError(LookupError):
    pass


class UserModelPersistenceError(RuntimeError):
    pass


def _last_four(api_key: str) -> str:
    return api_key[-4:] if len(api_key) >= 4 else api_key


def _record_from_row(row: dict[str, Any]) -> UserModelRecord:
    return UserModelRecord(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        display_name=row.get("display_name"),
        provider=row["provider"],
        model=row["model"],
        base_url=row.get("base_url"),
        enabled=bool(row.get("enabled", True)),
        has_api_key=bool(row.get("api_key_ref")),
        api_key_last_four=row.get("api_key_last_four"),
        created_at=row.get("created_at").isoformat() if row.get("created_at") else None,
        updated_at=row.get("updated_at").isoformat() if row.get("updated_at") else None,
    )


def to_model_config(row: dict[str, Any], *, secret_store: ModelSecretStore | None = None) -> ModelConfig:
    provider = row["provider"]
    if provider not in PROVIDER_USE_MAP:
        raise UserModelValidationError(f"Unsupported provider: {provider}")

    kwargs: dict[str, Any] = {
        "name": row["name"],
        "display_name": row.get("display_name") or row["name"],
        "use": PROVIDER_USE_MAP[provider],
        "model": row["model"],
    }
    base_url = (row.get("base_url") or "").strip()
    if base_url:
        kwargs["base_url"] = base_url

    api_key_ref = row.get("api_key_ref")
    if api_key_ref:
        store = secret_store or ModelSecretStore()
        kwargs["api_key"] = store.decrypt_api_key(api_key_ref)

    if provider == "anthropic":
        kwargs.setdefault("max_tokens", 8192)

    return ModelConfig(**kwargs)


class UserModelService:
    def __init__(self, repository: UserModelRepository, *, secret_store: ModelSecretStore | None = None) -> None:
        self._repo = repository
        self._secrets = secret_store or ModelSecretStore()

    async def list_models(self, user_id: str) -> list[UserModelRecord]:
        rows = await self._repo.list_for_user(user_id)
        return [_record_from_row(row) for row in rows]

    async def create_model(self, user_id: str, payload: UserModelCreateRequest) -> UserModelRecord:
        values: dict[str, Any] = {
            "user_id": user_id,
            "name": payload.name.strip(),
            "display_name": payload.display_name.strip() if payload.display_name else None,
            "provider": payload.provider,
            "model": payload.model.strip(),
            "base_url": (payload.base_url or DEFAULT_BASE_URLS[payload.provider]).strip() or None,
            "enabled": payload.enabled,
        }
        api_key = (payload.api_key or "").strip()
        if api_key:
            values["api_key_ref"] = self._secrets.encrypt_api_key(api_key)
            values["api_key_last_four"] = _last_four(api_key)
        try:
            row = await self._repo.create(values)
        except ValueError as exc:
            raise UserModelValidationError(str(exc)) from exc
        invalidate_user_model_cache(user_id)
        return _record_from_row(row)

    async def update_model(self, user_id: str, model_id: str, payload: UserModelUpdateRequest) -> UserModelRecord:
        existing = await self._repo.get(model_id, user_id=user_id)
        if existing is None:
            raise UserModelNotFoundError(f"Model {model_id!r} not found")

        values: dict[str, Any] = {}
        if payload.name is not None:
            values["name"] = payload.name.strip()
        if payload.display_name is not None:
            values["display_name"] = payload.display_name.strip() or None
        if payload.provider is not None:
            values["provider"] = payload.provider
        if payload.model is not None:
            values["model"] = payload.model.strip()
        if payload.base_url is not None:
            values["base_url"] = payload.base_url.strip() or None
        if payload.enabled is not None:
            values["enabled"] = payload.enabled

        api_key = payload.api_key
        if api_key is not None:
            if api_key == MASKED_API_KEY or api_key == "":
                pass
            else:
                values["api_key_ref"] = self._secrets.encrypt_api_key(api_key.strip())
                values["api_key_last_four"] = _last_four(api_key.strip())

        try:
            row = await self._repo.update(model_id, values, user_id=user_id)
        except ValueError as exc:
            raise UserModelValidationError(str(exc)) from exc
        if row is None:
            raise UserModelNotFoundError(f"Model {model_id!r} not found")
        invalidate_user_model_cache(user_id)
        return _record_from_row(row)

    async def delete_model(self, user_id: str, model_id: str) -> None:
        deleted = await self._repo.delete(model_id, user_id=user_id)
        if not deleted:
            raise UserModelNotFoundError(f"Model {model_id!r} not found")
        invalidate_user_model_cache(user_id)

    async def list_model_configs(self, user_id: str, *, include_disabled: bool = False) -> list[ModelConfig]:
        rows = await self._repo.list_for_user(user_id, include_disabled=include_disabled)
        configs: list[ModelConfig] = []
        for row in rows:
            if not include_disabled and not row.get("enabled", True):
                continue
            try:
                configs.append(to_model_config(row, secret_store=self._secrets))
            except Exception:
                continue
        return configs


def make_user_model_service(session_factory=None) -> UserModelService:
    sf = session_factory or get_session_factory()
    if sf is None:
        raise UserModelPersistenceError("User model persistence is not available when database.backend=memory")
    return UserModelService(UserModelRepository(sf))
