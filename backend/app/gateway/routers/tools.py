import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from deerflow.config.extensions_config import ExtensionsConfig, reload_extensions_config
from deerflow.tools.image_generation import (
    get_effective_image_generation_config,
    provider_metadata_as_dict,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tools"])

_MASKED_VALUE = "***"


class ImageGenerationProviderMetadataResponse(BaseModel):
    id: str
    display_name: str
    default_base_url: str
    default_model: str
    models: list[str] = Field(default_factory=list)
    supported_parameters: list[str] = Field(default_factory=list)
    required_parameters: list[str] = Field(default_factory=list)
    size_options: list[str] = Field(default_factory=list)
    quality_options: list[str] = Field(default_factory=list)
    style_options: list[str] = Field(default_factory=list)
    moderation_options: list[str] = Field(default_factory=list)
    background_options: list[str] = Field(default_factory=list)
    max_images: int
    api_key_label: str
    docs_url: str = ""


class ImageGenerationProviderConfigResponse(BaseModel):
    enabled: bool = False
    provider: str
    display_name: str = ""
    api_key: str | None = None
    has_api_key: bool = False
    base_url: str = ""
    model: str = ""
    timeout_seconds: float = 120.0
    trust_env: bool = False
    params: dict[str, Any] = Field(default_factory=dict)
    metadata: ImageGenerationProviderMetadataResponse | None = None


class ImageGenerationConfigResponse(BaseModel):
    enabled: bool = False
    default_provider: str | None = None
    output_subdir: str = "generated-images"
    providers: dict[str, ImageGenerationProviderConfigResponse] = Field(default_factory=dict)
    provider_metadata: dict[str, ImageGenerationProviderMetadataResponse] = Field(default_factory=dict)


class ImageGenerationProviderConfigUpdate(BaseModel):
    enabled: bool = False
    provider: str | None = None
    display_name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    timeout_seconds: float = 120.0
    trust_env: bool = False
    params: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")


class ImageGenerationConfigUpdateRequest(BaseModel):
    enabled: bool = False
    default_provider: str | None = None
    output_subdir: str = "generated-images"
    providers: dict[str, ImageGenerationProviderConfigUpdate] = Field(default_factory=dict)


def _extensions_config_path_for_write() -> Path:
    config_path = ExtensionsConfig.resolve_config_path()
    if config_path is not None:
        return config_path
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "extensions_config.json"


def _load_raw_extensions_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="extensions_config.json must contain a JSON object")
    return data


def _provider_response(name: str, provider: Any, metadata_by_id: dict[str, dict[str, Any]]) -> ImageGenerationProviderConfigResponse:
    adapter_id = provider.provider or name
    metadata = metadata_by_id.get(adapter_id)
    api_key = provider.api_key or ""
    return ImageGenerationProviderConfigResponse(
        enabled=provider.enabled,
        provider=adapter_id,
        display_name=provider.display_name or metadata.get("display_name", name) if metadata else provider.display_name or name,
        api_key=_MASKED_VALUE if api_key else None,
        has_api_key=bool(api_key),
        base_url=provider.base_url or metadata.get("default_base_url", "") if metadata else provider.base_url or "",
        model=provider.model or metadata.get("default_model", "") if metadata else provider.model or "",
        timeout_seconds=provider.timeout_seconds,
        trust_env=provider.trust_env,
        params=provider.params,
        metadata=ImageGenerationProviderMetadataResponse(**metadata) if metadata else None,
    )


def _config_response() -> ImageGenerationConfigResponse:
    image_config = get_effective_image_generation_config()
    metadata_by_id = provider_metadata_as_dict()
    metadata_response = {provider_id: ImageGenerationProviderMetadataResponse(**metadata) for provider_id, metadata in metadata_by_id.items()}
    return ImageGenerationConfigResponse(
        enabled=image_config.enabled,
        default_provider=image_config.default_provider,
        output_subdir=image_config.output_subdir,
        providers={name: _provider_response(name, provider, metadata_by_id) for name, provider in image_config.providers.items()},
        provider_metadata=metadata_response,
    )


def _merge_api_key(name: str, incoming: ImageGenerationProviderConfigUpdate, raw_providers: dict[str, Any]) -> str | None:
    raw_provider = raw_providers.get(name)
    existing = raw_provider.get("api_key") if isinstance(raw_provider, dict) else None
    if incoming.api_key is None or incoming.api_key == _MASKED_VALUE:
        return existing
    if incoming.api_key == "":
        return None
    return incoming.api_key


def _provider_update_to_raw(
    name: str,
    incoming: ImageGenerationProviderConfigUpdate,
    raw_providers: dict[str, Any],
) -> dict[str, Any]:
    data = {
        "enabled": incoming.enabled,
        "provider": incoming.provider or name,
        "display_name": incoming.display_name or "",
        "base_url": incoming.base_url or "",
        "model": incoming.model or "",
        "timeout_seconds": incoming.timeout_seconds,
        "trust_env": incoming.trust_env,
        "params": incoming.params,
    }
    api_key = _merge_api_key(name, incoming, raw_providers)
    if api_key:
        data["api_key"] = api_key
    return data


@router.get(
    "/tools/image-generation/config",
    response_model=ImageGenerationConfigResponse,
    summary="Get image generation tool configuration",
)
async def get_image_generation_configuration() -> ImageGenerationConfigResponse:
    return _config_response()


@router.put(
    "/tools/image-generation/config",
    response_model=ImageGenerationConfigResponse,
    summary="Update image generation tool configuration",
)
async def update_image_generation_configuration(
    request: ImageGenerationConfigUpdateRequest,
) -> ImageGenerationConfigResponse:
    try:
        config_path = _extensions_config_path_for_write()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        raw_data = _load_raw_extensions_config(config_path)
        raw_image_generation = raw_data.get("imageGeneration")
        if not isinstance(raw_image_generation, dict):
            raw_image_generation = {}
        raw_providers = raw_image_generation.get("providers")
        if not isinstance(raw_providers, dict):
            raw_providers = {}

        raw_data["imageGeneration"] = {
            "enabled": request.enabled,
            "defaultProvider": request.default_provider,
            "outputSubdir": request.output_subdir,
            "providers": {name: _provider_update_to_raw(name, provider, raw_providers) for name, provider in request.providers.items()},
        }

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=2)

        reload_extensions_config()
        return _config_response()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update image generation configuration: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update image generation configuration: {exc}") from exc
