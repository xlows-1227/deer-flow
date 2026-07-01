from __future__ import annotations

from typing import Any

from deerflow.config.effective_config import invalidate_user_extensions_cache
from deerflow.config.extensions_config import ImageGenerationConfig, ImageGenerationProviderConfig, default_image_generation_providers
from deerflow.extensions_user.schemas import (
    MASKED_VALUE,
    ImageConfigResponse,
    ImageConfigUpdateRequest,
    ImageProviderRecord,
)
from deerflow.extensions_user.secrets import ExtensionSecretStore
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.user_extension import UserImageProviderRepository, UserImageSettingsRepository
from deerflow.tools.image_generation import provider_metadata_as_dict


class UserImageValidationError(ValueError):
    pass


class UserImagePersistenceError(RuntimeError):
    pass


def _last_four(api_key: str) -> str:
    return api_key[-4:] if len(api_key) >= 4 else api_key


class UserImageService:
    def __init__(
        self,
        settings_repo: UserImageSettingsRepository,
        provider_repo: UserImageProviderRepository,
        *,
        secret_store: ExtensionSecretStore | None = None,
    ) -> None:
        self._settings = settings_repo
        self._providers = provider_repo
        self._secrets = secret_store or ExtensionSecretStore()

    async def get_config_view(self, user_id: str) -> ImageConfigResponse:
        metadata_by_id = provider_metadata_as_dict()
        settings = await self._settings.get(user_id)
        provider_rows = await self._providers.list_for_user(user_id)
        provider_by_name = {row["provider"]: row for row in provider_rows}

        providers: dict[str, ImageProviderRecord] = {}
        for name, defaults in default_image_generation_providers().items():
            metadata = metadata_by_id.get(name, {})
            row = provider_by_name.get(name)
            api_key_ref = row.get("api_key_ref") if row else None
            providers[name] = ImageProviderRecord(
                provider=name,
                enabled=bool(row.get("enabled", False)) if row else False,
                display_name=metadata.get("display_name", name),
                api_key=MASKED_VALUE if api_key_ref else None,
                has_api_key=bool(api_key_ref),
                base_url=(row.get("base_url") if row else None) or metadata.get("default_base_url", ""),
                model=(row.get("model") if row else None) or metadata.get("default_model", ""),
                timeout_seconds=float(row.get("timeout_seconds", 120.0)) if row else 120.0,
                trust_env=bool(row.get("trust_env", False)) if row else False,
                params=dict(row.get("params") or {}) if row else {},
            )

        return ImageConfigResponse(
            enabled=bool(settings.get("enabled", False)) if settings else False,
            default_provider=(settings.get("default_provider") if settings else None) or "openai",
            output_subdir=(settings.get("output_subdir") if settings else None) or "generated-images",
            providers=providers,
            provider_metadata=metadata_by_id,
        )

    async def update_config(self, user_id: str, payload: ImageConfigUpdateRequest) -> ImageConfigResponse:
        await self._settings.upsert(
            user_id,
            {
                "enabled": payload.enabled,
                "default_provider": payload.default_provider,
                "output_subdir": payload.output_subdir,
            },
        )

        for name, provider_update in payload.providers.items():
            values: dict[str, Any] = {
                "enabled": provider_update.enabled,
                "base_url": provider_update.base_url,
                "model": provider_update.model,
                "timeout_seconds": provider_update.timeout_seconds,
                "trust_env": provider_update.trust_env,
                "params": provider_update.params,
            }
            api_key = provider_update.api_key
            if api_key is not None and api_key not in (MASKED_VALUE, ""):
                values["api_key_ref"] = self._secrets.encrypt_api_key(api_key.strip())
                values["api_key_last_four"] = _last_four(api_key.strip())
            elif api_key == "":
                values["api_key_ref"] = None
                values["api_key_last_four"] = None

            await self._providers.upsert_provider(user_id, name, values)

        invalidate_user_extensions_cache(user_id)
        return await self.get_config_view(user_id)

    async def build_user_image_config(self, user_id: str) -> ImageGenerationConfig:
        settings = await self._settings.get(user_id)
        provider_rows = await self._providers.list_for_user(user_id)
        defaults = default_image_generation_providers()
        providers: dict[str, ImageGenerationProviderConfig] = {}

        for name, default_provider in defaults.items():
            row = next((item for item in provider_rows if item["provider"] == name), None)
            if row is None:
                providers[name] = default_provider.model_copy(update={"enabled": False, "api_key": None})
                continue

            api_key = None
            if row.get("api_key_ref"):
                try:
                    api_key = self._secrets.decrypt_api_key(row["api_key_ref"])
                except Exception:
                    api_key = None

            providers[name] = default_provider.model_copy(
                update={
                    "enabled": bool(row.get("enabled", False)),
                    "api_key": api_key,
                    "base_url": row.get("base_url") or default_provider.base_url,
                    "model": row.get("model") or default_provider.model,
                    "timeout_seconds": float(row.get("timeout_seconds", 120.0)),
                    "trust_env": bool(row.get("trust_env", False)),
                    "params": dict(row.get("params") or {}),
                }
            )

        return ImageGenerationConfig(
            enabled=bool(settings.get("enabled", False)) if settings else False,
            default_provider=(settings.get("default_provider") if settings else None) or "openai",
            output_subdir=(settings.get("output_subdir") if settings else None) or "generated-images",
            providers=providers,
        )


def make_user_image_service(session_factory=None) -> UserImageService:
    sf = session_factory or get_session_factory()
    if sf is None:
        raise UserImagePersistenceError("User image persistence is not available when database.backend=memory")
    return UserImageService(UserImageSettingsRepository(sf), UserImageProviderRepository(sf))
