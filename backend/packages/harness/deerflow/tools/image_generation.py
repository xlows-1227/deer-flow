"""Provider adapters for the built-in image generation tool."""

from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
from dataclasses import dataclass
from typing import Any

import httpx

from deerflow.config.extensions_config import (
    ExtensionsConfig,
    ImageGenerationConfig,
    ImageGenerationProviderConfig,
    _default_image_generation_providers,
)

logger = logging.getLogger(__name__)


class ImageGenerationConfigError(ValueError):
    """Raised when image generation cannot proceed because configuration is incomplete."""


class ImageGenerationProviderError(RuntimeError):
    """Raised when a provider request fails."""


@dataclass(frozen=True)
class ImageGenerationProviderMetadata:
    id: str
    display_name: str
    default_base_url: str
    default_model: str
    models: tuple[str, ...]
    supported_parameters: tuple[str, ...]
    required_parameters: tuple[str, ...] = ("prompt",)
    size_options: tuple[str, ...] = ()
    quality_options: tuple[str, ...] = ()
    style_options: tuple[str, ...] = ()
    moderation_options: tuple[str, ...] = ()
    background_options: tuple[str, ...] = ()
    max_images: int = 4
    api_key_label: str = "API key"
    docs_url: str = ""


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime_type: str
    revised_prompt: str | None = None


PROVIDER_METADATA: dict[str, ImageGenerationProviderMetadata] = {
    "openai": ImageGenerationProviderMetadata(
        id="openai",
        display_name="OpenAI",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-image-1",
        models=("gpt-image-1", "dall-e-3", "dall-e-2"),
        supported_parameters=("prompt", "model", "size", "n", "quality", "style"),
        size_options=("1024x1024", "1024x1536", "1536x1024", "1792x1024", "1024x1792"),
        quality_options=("auto", "standard", "hd", "low", "medium", "high"),
        style_options=("vivid", "natural"),
        max_images=4,
        docs_url="https://platform.openai.com/docs/api-reference/images/create",
    ),
    "stability": ImageGenerationProviderMetadata(
        id="stability",
        display_name="Stability AI",
        default_base_url="https://api.stability.ai/v2beta",
        default_model="sd3.5-large",
        models=("sd3.5-large", "sd3.5-large-turbo", "sd3.5-medium", "sd3-large", "core", "ultra"),
        supported_parameters=("prompt", "model", "size", "n", "negative_prompt", "seed", "style"),
        size_options=("1:1", "16:9", "9:16", "4:5", "5:4", "3:2", "2:3", "21:9", "9:21"),
        style_options=(
            "3d-model",
            "analog-film",
            "anime",
            "cinematic",
            "comic-book",
            "digital-art",
            "enhance",
            "fantasy-art",
            "isometric",
            "line-art",
            "low-poly",
            "modeling-compound",
            "neon-punk",
            "origami",
            "photographic",
            "pixel-art",
            "tile-texture",
        ),
        max_images=4,
        docs_url="https://platform.stability.ai/docs/api-reference",
    ),
    "volcengine": ImageGenerationProviderMetadata(
        id="volcengine",
        display_name="Volcengine Ark",
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-seedream-3-0-t2i-250415",
        models=("doubao-seedream-3-0-t2i-250415", "doubao-seedream-4-0-250828"),
        supported_parameters=("prompt", "model", "size", "n", "seed"),
        size_options=("1024x1024", "1024x1792", "1792x1024", "1280x720", "720x1280"),
        max_images=4,
        docs_url="https://www.volcengine.com/docs/82379",
    ),
    "aihubmix": ImageGenerationProviderMetadata(
        id="aihubmix",
        display_name="Aihubmix",
        default_base_url="https://aihubmix.com/v1",
        default_model="openai/gpt-image-2-free",
        models=("openai/gpt-image-2-free", "gemini-3.1-flash-image-preview-free"),
        supported_parameters=("prompt", "model", "size", "n", "quality", "moderation", "background"),
        size_options=(
            "1024x1024",
            "1024x1536",
            "1536x1024",
            "1792x1024",
            "1024x1792",
            "1:1",
            "16:9",
            "9:16",
            "4:3",
            "3:4",
            "3:2",
            "2:3",
        ),
        quality_options=("auto", "low", "medium", "high"),
        moderation_options=("auto", "low"),
        background_options=("auto", "transparent", "opaque"),
        max_images=4,
        docs_url="https://aihubmix.com",
    ),
    "minimax": ImageGenerationProviderMetadata(
        id="minimax",
        display_name="MiniMax",
        default_base_url="https://api.minimaxi.com/v1",
        default_model="image-01",
        models=("image-01",),
        supported_parameters=("prompt", "model", "size"),
        size_options=("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"),
        max_images=1,
    ),
    "custom_openai_compatible": ImageGenerationProviderMetadata(
        id="custom_openai_compatible",
        display_name="OpenAI-compatible",
        default_base_url="",
        default_model="",
        models=(),
        supported_parameters=("prompt", "model", "size", "n", "quality", "style", "negative_prompt", "seed"),
        max_images=4,
    ),
}


def provider_metadata_as_dict() -> dict[str, dict[str, Any]]:
    return {
        provider_id: {
            "id": metadata.id,
            "display_name": metadata.display_name,
            "default_base_url": metadata.default_base_url,
            "default_model": metadata.default_model,
            "models": list(metadata.models),
            "supported_parameters": list(metadata.supported_parameters),
            "required_parameters": list(metadata.required_parameters),
            "size_options": list(metadata.size_options),
            "quality_options": list(metadata.quality_options),
            "style_options": list(metadata.style_options),
            "moderation_options": list(metadata.moderation_options),
            "background_options": list(metadata.background_options),
            "max_images": metadata.max_images,
            "api_key_label": metadata.api_key_label,
            "docs_url": metadata.docs_url,
        }
        for provider_id, metadata in PROVIDER_METADATA.items()
    }


def get_effective_image_generation_config(config: ExtensionsConfig | None = None) -> ImageGenerationConfig:
    """Merge user image-generation settings with built-in provider defaults."""
    config = config or ExtensionsConfig.from_file()
    image_config = config.image_generation
    providers = _default_image_generation_providers()

    for name, provider in image_config.providers.items():
        base = providers.get(name)
        if base is None:
            base = ImageGenerationProviderConfig(provider=provider.provider or name, display_name=provider.display_name or name)
        update = provider.model_dump(exclude_unset=True)
        if not update.get("provider"):
            update["provider"] = provider.provider or name
        providers[name] = base.model_copy(update=update)

    return image_config.model_copy(
        update={
            "providers": providers,
            "default_provider": image_config.default_provider or "openai",
        }
    )


def has_enabled_image_generation_provider(config: ExtensionsConfig | None = None) -> bool:
    image_config = get_effective_image_generation_config(config)
    if not image_config.enabled:
        return False
    return any(provider.enabled for provider in image_config.providers.values())


def _clean_string(value: str | None) -> str:
    return (value or "").strip()


def _select_provider_config(
    image_config: ImageGenerationConfig,
    requested_provider: str | None,
) -> tuple[str, ImageGenerationProviderConfig, ImageGenerationProviderMetadata]:
    provider_name = _clean_string(requested_provider) or _clean_string(image_config.default_provider)
    if not provider_name:
        enabled = [name for name, provider in image_config.providers.items() if provider.enabled]
        raise ImageGenerationConfigError(f"Ask the user which image provider to use. Enabled providers: {', '.join(enabled) or 'none'}.")

    provider_config = image_config.providers.get(provider_name)
    if provider_config is None:
        available = ", ".join(sorted(image_config.providers))
        raise ImageGenerationConfigError(f"Unknown image provider '{provider_name}'. Ask the user to choose one of: {available}.")
    if not provider_config.enabled:
        raise ImageGenerationConfigError(f"Image provider '{provider_name}' is disabled. Ask the user to enable it in Settings > Tools > Image generation, or choose another provider.")

    adapter_id = provider_config.provider or provider_name
    metadata = PROVIDER_METADATA.get(adapter_id)
    if metadata is None:
        available = ", ".join(sorted(PROVIDER_METADATA))
        raise ImageGenerationConfigError(f"Image provider '{provider_name}' uses unsupported adapter '{adapter_id}'. Supported adapters: {available}.")
    return provider_name, provider_config, metadata


def _validate_choice(label: str, value: str | None, choices: tuple[str, ...]) -> None:
    if not value or not choices:
        return
    if value not in choices:
        raise ImageGenerationConfigError(f"Unsupported {label} '{value}'. Ask the user to choose one of: {', '.join(choices)}.")


def _validated_request_params(
    metadata: ImageGenerationProviderMetadata,
    *,
    prompt: str,
    model: str,
    size: str | None,
    n: int,
    quality: str | None,
    style: str | None,
    moderation: str | None,
    background: str | None,
    negative_prompt: str | None,
    seed: int | None,
    extra_params: dict[str, Any] | None,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise ImageGenerationConfigError("Ask the user for the image prompt before calling generate_image.")
    if not model.strip():
        raise ImageGenerationConfigError("No image model is configured. Ask the user to choose a default image model in Settings > Tools > Image generation.")
    if n < 1:
        raise ImageGenerationConfigError("Image count must be at least 1.")
    if n > metadata.max_images:
        raise ImageGenerationConfigError(f"{metadata.display_name} can generate at most {metadata.max_images} image(s) per tool call. Ask the user for a smaller count.")

    candidate_params = {
        "size": size,
        "quality": quality,
        "style": style,
        "moderation": moderation,
        "background": background,
        "negative_prompt": negative_prompt,
        "seed": seed,
    }
    unsupported = [name for name, value in candidate_params.items() if value is not None and name not in metadata.supported_parameters]
    if unsupported:
        supported = ", ".join(metadata.supported_parameters)
        raise ImageGenerationConfigError(f"{metadata.display_name} does not support parameter(s): {', '.join(unsupported)}. Supported parameters: {supported}. Ask the user again using only supported options.")

    _validate_choice("size/aspect ratio", size, metadata.size_options)
    _validate_choice("quality", quality, metadata.quality_options)
    _validate_choice("style", style, metadata.style_options)
    _validate_choice("moderation", moderation, metadata.moderation_options)
    _validate_choice("background", background, metadata.background_options)

    params: dict[str, Any] = {"prompt": prompt, "model": model.strip(), "n": n}
    for name, value in candidate_params.items():
        if value is not None:
            params[name] = value
    if extra_params:
        params.update(extra_params)
    return params


def _detect_mime_type(data: bytes, fallback: str | None = None) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback or "image/png"


def _decode_base64_image(value: str) -> bytes:
    if "," in value and value.strip().lower().startswith("data:"):
        value = value.split(",", 1)[1]
    try:
        return base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ImageGenerationProviderError("Provider returned invalid base64 image data") from exc


def _raise_http_error(response: httpx.Response, provider_name: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:800] if exc.response is not None else str(exc)
        raise ImageGenerationProviderError(f"{provider_name} API returned HTTP {response.status_code}: {detail}") from exc


def _download_image(client: httpx.Client, url: str, provider_name: str) -> GeneratedImage:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        logger.exception("Image generation image download failed provider=%s url=%s error_type=%s", provider_name, url, type(exc).__name__)
        raise ImageGenerationProviderError(f"{provider_name} image download failed: {type(exc).__name__}: {exc}") from exc
    _raise_http_error(response, provider_name)
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    return GeneratedImage(data=response.content, mime_type=_detect_mime_type(response.content, content_type or None))


def _is_probably_base64_image(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.lower().startswith("data:image/"):
        return True
    if len(stripped) < 40 or " " in stripped:
        return False
    try:
        decoded = base64.b64decode(stripped.split(",", 1)[-1], validate=False)
    except (binascii.Error, ValueError):
        return False
    return _detect_mime_type(decoded, "application/octet-stream") in {"image/png", "image/jpeg", "image/webp"}


def _images_from_mixed_json_value(client: httpx.Client, value: Any, provider_name: str) -> list[GeneratedImage]:
    images: list[GeneratedImage] = []
    if isinstance(value, dict):
        revised_prompt = value.get("revised_prompt") if isinstance(value.get("revised_prompt"), str) else None
        for key in ("b64_json", "base64", "image_base64", "bytesBase64"):
            image_value = value.get(key)
            if isinstance(image_value, str) and image_value:
                data = _decode_base64_image(image_value)
                images.append(GeneratedImage(data=data, mime_type=_detect_mime_type(data), revised_prompt=revised_prompt))
            elif isinstance(image_value, (dict, list)):
                images.extend(_images_from_mixed_json_value(client, image_value, provider_name))
        for key in ("url", "image_url"):
            image_url = value.get(key)
            if isinstance(image_url, str) and image_url:
                downloaded = _download_image(client, image_url, provider_name)
                images.append(GeneratedImage(data=downloaded.data, mime_type=downloaded.mime_type, revised_prompt=revised_prompt))
        for key in ("image", "images", "output", "result", "data"):
            nested = value.get(key)
            if nested is not None:
                nested_images = _images_from_mixed_json_value(client, nested, provider_name)
                if revised_prompt:
                    nested_images = [GeneratedImage(data=image.data, mime_type=image.mime_type, revised_prompt=image.revised_prompt or revised_prompt) for image in nested_images]
                images.extend(nested_images)
    elif isinstance(value, list):
        for item in value:
            images.extend(_images_from_mixed_json_value(client, item, provider_name))
    elif isinstance(value, str):
        if value.startswith(("http://", "https://")):
            images.append(_download_image(client, value, provider_name))
        elif _is_probably_base64_image(value):
            data = _decode_base64_image(value)
            images.append(GeneratedImage(data=data, mime_type=_detect_mime_type(data)))
    return images


def _images_from_openai_response(client: httpx.Client, response: httpx.Response, provider_name: str) -> list[GeneratedImage]:
    _raise_http_error(response, provider_name)
    try:
        body = response.json()
    except ValueError as exc:
        raise ImageGenerationProviderError(f"{provider_name} returned a non-JSON image response") from exc

    entries = body.get("data")
    if not isinstance(entries, list) or not entries:
        raise ImageGenerationProviderError(f"{provider_name} returned no images")

    images: list[GeneratedImage] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        revised_prompt = entry.get("revised_prompt") if isinstance(entry.get("revised_prompt"), str) else None
        b64_json = entry.get("b64_json")
        if isinstance(b64_json, str) and b64_json:
            data = _decode_base64_image(b64_json)
            images.append(GeneratedImage(data=data, mime_type=_detect_mime_type(data), revised_prompt=revised_prompt))
            continue
        url = entry.get("url")
        if isinstance(url, str) and url:
            downloaded = _download_image(client, url, provider_name)
            images.append(GeneratedImage(data=downloaded.data, mime_type=downloaded.mime_type, revised_prompt=revised_prompt))

    if not images:
        raise ImageGenerationProviderError(f"{provider_name} response did not include b64_json or url image data")
    return images


def _openai_payload(params: dict[str, Any], metadata: ImageGenerationProviderMetadata, provider_defaults: dict[str, Any]) -> dict[str, Any]:
    payload = dict(provider_defaults)
    payload.update({"model": params["model"], "prompt": params["prompt"], "n": params["n"]})
    for name in ("size", "quality", "style", "negative_prompt", "seed"):
        if name in params and name in metadata.supported_parameters:
            payload[name] = params[name]
    return payload


def _aihubmix_payload(params: dict[str, Any], metadata: ImageGenerationProviderMetadata, provider_defaults: dict[str, Any]) -> dict[str, Any]:
    input_payload = dict(provider_defaults)
    input_payload.update({"prompt": params["prompt"], "n": params["n"]})
    for name in ("size", "quality", "moderation", "background", "style", "negative_prompt", "seed"):
        if name in params and name in metadata.supported_parameters:
            input_payload[name] = params[name]
    return {"input": input_payload}


def _minimax_payload(params: dict[str, Any], provider_defaults: dict[str, Any]) -> dict[str, Any]:
    payload = dict(provider_defaults)
    payload.update(
        {
            "model": params["model"],
            "prompt": params["prompt"],
            "response_format": "base64",
        }
    )
    if params.get("size"):
        payload["aspect_ratio"] = params["size"]
    return payload


AIHUBMIX_GEMINI_BASE_URL = "https://aihubmix.com/gemini/v1beta"

_PIXEL_SIZE_TO_ASPECT_RATIO = {
    "1024x1024": "1:1",
    "1024x1536": "2:3",
    "1536x1024": "3:2",
    "1792x1024": "16:9",
    "1024x1792": "9:16",
}


def _is_aihubmix_gemini_model(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith("gemini-") or "/gemini-" in normalized


def _aihubmix_gemini_aspect_ratio(size: str | None) -> str:
    if not size:
        return "1:1"
    if ":" in size:
        return size
    return _PIXEL_SIZE_TO_ASPECT_RATIO.get(size, "1:1")


def _aihubmix_gemini_image_size(quality: str | None, provider_defaults: dict[str, Any]) -> str:
    if isinstance(provider_defaults.get("imageSize"), str) and provider_defaults["imageSize"].strip():
        return provider_defaults["imageSize"].strip()
    if isinstance(provider_defaults.get("image_size"), str) and provider_defaults["image_size"].strip():
        return provider_defaults["image_size"].strip()
    quality_map = {"low": "1k", "medium": "1k", "high": "2k", "auto": "1k"}
    if quality and quality in quality_map:
        return quality_map[quality]
    return "1k"


def _aihubmix_gemini_payload(params: dict[str, Any], provider_defaults: dict[str, Any]) -> dict[str, Any]:
    image_config: dict[str, Any] = {
        "aspectRatio": _aihubmix_gemini_aspect_ratio(params.get("size")),
        "imageSize": _aihubmix_gemini_image_size(params.get("quality"), provider_defaults),
    }
    generation_config = dict(provider_defaults.get("generationConfig") or provider_defaults.get("generation_config") or {})
    generation_config.setdefault("responseModalities", ["TEXT", "IMAGE"])
    generation_config["imageConfig"] = {**(generation_config.get("imageConfig") or {}), **image_config}
    return {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": params["prompt"]}],
            }
        ],
        "generationConfig": generation_config,
    }


def _images_from_aihubmix_gemini_body(body: dict[str, Any]) -> list[GeneratedImage]:
    images: list[GeneratedImage] = []
    revised_prompt: str | None = None

    def _append_inline_part(inline: dict[str, Any]) -> None:
        nonlocal revised_prompt
        data_value = inline.get("data")
        if not isinstance(data_value, str) or not data_value:
            return
        data = _decode_base64_image(data_value)
        mime_type = inline.get("mime_type") or inline.get("mimeType")
        images.append(
            GeneratedImage(
                data=data,
                mime_type=str(mime_type) if isinstance(mime_type, str) and mime_type else _detect_mime_type(data),
                revised_prompt=revised_prompt,
            )
        )

    def _consume_parts(parts: list[Any]) -> None:
        nonlocal revised_prompt
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                revised_prompt = text.strip()
            inline = part.get("inline_data") or part.get("inlineData")
            if isinstance(inline, dict):
                _append_inline_part(inline)

    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            multi_mod_content = message.get("multi_mod_content")
            if isinstance(multi_mod_content, list):
                _consume_parts(multi_mod_content)
                if images:
                    return images

    candidates = body.get("candidates")
    if isinstance(candidates, list) and candidates:
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, list):
                _consume_parts(parts)
                if images:
                    return images

    return images


def _request_log_context(provider_name: str, metadata: ImageGenerationProviderMetadata, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": provider_name,
        "adapter": metadata.id,
        "model": params.get("model"),
        "endpoint": endpoint,
        "prompt_length": len(str(params.get("prompt", ""))),
        "n": params.get("n"),
        "size": params.get("size"),
        "quality": params.get("quality"),
        "style": params.get("style"),
        "moderation": params.get("moderation"),
        "background": params.get("background"),
        "has_negative_prompt": bool(params.get("negative_prompt")),
        "has_seed": params.get("seed") is not None,
    }


def _log_provider_request_started(context: dict[str, Any]) -> None:
    logger.info(
        "Image generation request started provider=%s adapter=%s model=%s endpoint=%s prompt_length=%s n=%s size=%s quality=%s style=%s moderation=%s background=%s has_negative_prompt=%s has_seed=%s",
        context["provider"],
        context["adapter"],
        context["model"],
        context["endpoint"],
        context["prompt_length"],
        context["n"],
        context["size"],
        context["quality"],
        context["style"],
        context["moderation"],
        context["background"],
        context["has_negative_prompt"],
        context["has_seed"],
    )


def _post_provider_request(
    client: httpx.Client,
    *,
    provider_name: str,
    metadata: ImageGenerationProviderMetadata,
    endpoint: str,
    params: dict[str, Any],
    headers: dict[str, str],
    json_payload: dict[str, Any] | None = None,
    files: dict[str, tuple[None, str]] | None = None,
) -> httpx.Response:
    context = _request_log_context(provider_name, metadata, endpoint, params)
    _log_provider_request_started(context)
    try:
        if files is not None:
            return client.post(endpoint, headers=headers, files=files)
        return client.post(endpoint, headers=headers, json=json_payload)
    except httpx.HTTPError as exc:
        logger.exception(
            "Image generation HTTP request failed provider=%s adapter=%s model=%s endpoint=%s error_type=%s",
            context["provider"],
            context["adapter"],
            context["model"],
            context["endpoint"],
            type(exc).__name__,
        )
        raise ImageGenerationProviderError(f"{metadata.display_name} request failed: {type(exc).__name__}: {exc}") from exc


def _generate_openai_compatible(
    *,
    client: httpx.Client,
    provider_name: str,
    metadata: ImageGenerationProviderMetadata,
    provider_config: ImageGenerationProviderConfig,
    params: dict[str, Any],
) -> list[GeneratedImage]:
    api_key = _clean_string(provider_config.api_key)
    if not api_key:
        raise ImageGenerationConfigError(f"{metadata.display_name} API key is not configured. Ask the user to add it in Settings > Tools > Image generation.")
    base_url = _clean_string(provider_config.base_url) or metadata.default_base_url
    if not base_url:
        raise ImageGenerationConfigError(f"{metadata.display_name} base URL is not configured. Ask the user to add it in Settings > Tools > Image generation.")

    payload = _openai_payload(params, metadata, provider_config.params)
    endpoint = f"{base_url.rstrip('/')}/images/generations"
    response = _post_provider_request(
        client,
        provider_name=provider_name,
        metadata=metadata,
        endpoint=endpoint,
        params=params,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json_payload=payload,
    )
    return _images_from_openai_response(client, response, provider_name)


def _generate_aihubmix_gemini(
    *,
    client: httpx.Client,
    provider_name: str,
    metadata: ImageGenerationProviderMetadata,
    provider_config: ImageGenerationProviderConfig,
    params: dict[str, Any],
) -> list[GeneratedImage]:
    api_key = _clean_string(provider_config.api_key)
    if not api_key:
        raise ImageGenerationConfigError("Aihubmix API key is not configured. Ask the user to add it in Settings > Tools > Image generation.")
    model = params["model"]
    count = int(params.get("n") or 1)
    payload = _aihubmix_gemini_payload(params, provider_config.params)
    endpoint = f"{AIHUBMIX_GEMINI_BASE_URL.rstrip('/')}/models/{model}:generateContent"
    images: list[GeneratedImage] = []
    for _ in range(count):
        response = _post_provider_request(
            client,
            provider_name=provider_name,
            metadata=metadata,
            endpoint=endpoint,
            params=params,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json_payload=payload,
        )
        _raise_http_error(response, provider_name)
        try:
            body = response.json()
        except ValueError as exc:
            raise ImageGenerationProviderError("Aihubmix Gemini returned a non-JSON image response") from exc
        batch = _images_from_aihubmix_gemini_body(body)
        if not batch:
            raise ImageGenerationProviderError("Aihubmix Gemini response did not include image data")
        images.extend(batch)
    return images


def _generate_aihubmix(
    *,
    client: httpx.Client,
    provider_name: str,
    metadata: ImageGenerationProviderMetadata,
    provider_config: ImageGenerationProviderConfig,
    params: dict[str, Any],
) -> list[GeneratedImage]:
    if _is_aihubmix_gemini_model(params["model"]):
        return _generate_aihubmix_gemini(
            client=client,
            provider_name=provider_name,
            metadata=metadata,
            provider_config=provider_config,
            params=params,
        )

    api_key = _clean_string(provider_config.api_key)
    if not api_key:
        raise ImageGenerationConfigError("Aihubmix API key is not configured. Ask the user to add it in Settings > Tools > Image generation.")
    base_url = _clean_string(provider_config.base_url) or metadata.default_base_url
    model = params["model"]
    payload = _aihubmix_payload(params, metadata, provider_config.params)
    endpoint = f"{base_url.rstrip('/')}/models/{model}/predictions"
    response = _post_provider_request(
        client,
        provider_name=provider_name,
        metadata=metadata,
        endpoint=endpoint,
        params=params,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        json_payload=payload,
    )
    _raise_http_error(response, provider_name)
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    if content_type.startswith("image/"):
        return [GeneratedImage(data=response.content, mime_type=_detect_mime_type(response.content, content_type))]

    try:
        body = response.json()
    except ValueError as exc:
        raise ImageGenerationProviderError("Aihubmix returned a non-JSON image response") from exc

    images = _images_from_mixed_json_value(client, body, provider_name)
    if not images:
        raise ImageGenerationProviderError("Aihubmix response did not include image data")
    return images


def _stability_endpoint(base_url: str, model: str) -> str:
    normalized = model.strip().lower()
    if normalized in {"core", "stable-image-core"}:
        return f"{base_url.rstrip('/')}/stable-image/generate/core"
    if normalized in {"ultra", "stable-image-ultra"}:
        return f"{base_url.rstrip('/')}/stable-image/generate/ultra"
    return f"{base_url.rstrip('/')}/stable-image/generate/sd3"


def _generate_stability(
    *,
    client: httpx.Client,
    provider_name: str,
    metadata: ImageGenerationProviderMetadata,
    provider_config: ImageGenerationProviderConfig,
    params: dict[str, Any],
) -> list[GeneratedImage]:
    api_key = _clean_string(provider_config.api_key)
    if not api_key:
        raise ImageGenerationConfigError("Stability AI API key is not configured. Ask the user to add it in Settings > Tools > Image generation.")
    base_url = _clean_string(provider_config.base_url) or metadata.default_base_url
    model = params["model"]
    count = int(params.get("n") or 1)

    form_defaults = dict(provider_config.params)
    form_defaults.setdefault("output_format", "png")
    form: dict[str, Any] = {
        **form_defaults,
        "prompt": params["prompt"],
    }
    if model.strip().lower() not in {"core", "stable-image-core", "ultra", "stable-image-ultra"}:
        form["model"] = model
    if params.get("size"):
        form["aspect_ratio"] = params["size"]
    if params.get("negative_prompt"):
        form["negative_prompt"] = params["negative_prompt"]
    if params.get("seed") is not None:
        form["seed"] = str(params["seed"])
    if params.get("style"):
        form["style_preset"] = params["style"]

    images: list[GeneratedImage] = []
    for _ in range(count):
        endpoint = _stability_endpoint(base_url, model)
        response = _post_provider_request(
            client,
            provider_name=provider_name,
            metadata=metadata,
            endpoint=endpoint,
            params=params,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "image/*"},
            files={key: (None, str(value)) for key, value in form.items() if value is not None},
        )
        _raise_http_error(response, provider_name)
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        if content_type == "application/json":
            body = response.json()
            b64_image = body.get("image") or body.get("b64_json")
            if not isinstance(b64_image, str):
                raise ImageGenerationProviderError("Stability AI returned JSON without image data")
            data = _decode_base64_image(b64_image)
            images.append(GeneratedImage(data=data, mime_type=_detect_mime_type(data)))
        else:
            images.append(GeneratedImage(data=response.content, mime_type=_detect_mime_type(response.content, content_type or None)))
    return images


def _generate_minimax(
    *,
    client: httpx.Client,
    provider_name: str,
    metadata: ImageGenerationProviderMetadata,
    provider_config: ImageGenerationProviderConfig,
    params: dict[str, Any],
) -> list[GeneratedImage]:
    api_key = _clean_string(provider_config.api_key)
    if not api_key:
        raise ImageGenerationConfigError("MiniMax API key is not configured. Ask the user to add it in Settings > Tools > Image generation.")
    base_url = _clean_string(provider_config.base_url) or metadata.default_base_url
    payload = _minimax_payload(params, provider_config.params)
    endpoint = f"{base_url.rstrip('/')}/image_generation"
    response = _post_provider_request(
        client,
        provider_name=provider_name,
        metadata=metadata,
        endpoint=endpoint,
        params=params,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json_payload=payload,
    )
    _raise_http_error(response, provider_name)
    try:
        body = response.json()
    except ValueError as exc:
        raise ImageGenerationProviderError("MiniMax returned a non-JSON image response") from exc

    images = _images_from_mixed_json_value(client, body, provider_name)
    if not images:
        raise ImageGenerationProviderError("MiniMax response did not include image data")
    return images


def generate_images(
    *,
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
    size: str | None = None,
    n: int = 1,
    quality: str | None = None,
    style: str | None = None,
    moderation: str | None = None,
    background: str | None = None,
    negative_prompt: str | None = None,
    seed: int | None = None,
    extra_params: dict[str, Any] | None = None,
    config: ExtensionsConfig | None = None,
) -> tuple[str, ImageGenerationProviderMetadata, list[GeneratedImage]]:
    image_config = get_effective_image_generation_config(config)
    if not image_config.enabled:
        raise ImageGenerationConfigError("Image generation is disabled. Ask the user to enable it in Settings > Tools > Image generation.")

    provider_name, provider_config, metadata = _select_provider_config(image_config, provider)
    selected_model = _clean_string(model) or _clean_string(provider_config.model) or metadata.default_model
    params = _validated_request_params(
        metadata,
        prompt=prompt,
        model=selected_model,
        size=_clean_string(size) or None,
        n=n,
        quality=_clean_string(quality) or None,
        style=_clean_string(style) or None,
        moderation=_clean_string(moderation) or None,
        background=_clean_string(background) or None,
        negative_prompt=_clean_string(negative_prompt) or None,
        seed=seed,
        extra_params=extra_params,
    )

    timeout = provider_config.timeout_seconds if provider_config.timeout_seconds > 0 else 120.0
    with httpx.Client(timeout=timeout, trust_env=provider_config.trust_env) as client:
        adapter_id = provider_config.provider or provider_name
        if adapter_id in {"openai", "volcengine", "custom_openai_compatible"}:
            images = _generate_openai_compatible(
                client=client,
                provider_name=provider_name,
                metadata=metadata,
                provider_config=provider_config,
                params=params,
            )
        elif adapter_id == "stability":
            images = _generate_stability(
                client=client,
                provider_name=provider_name,
                metadata=metadata,
                provider_config=provider_config,
                params=params,
            )
        elif adapter_id == "aihubmix":
            images = _generate_aihubmix(
                client=client,
                provider_name=provider_name,
                metadata=metadata,
                provider_config=provider_config,
                params=params,
            )
        elif adapter_id == "minimax":
            images = _generate_minimax(
                client=client,
                provider_name=provider_name,
                metadata=metadata,
                provider_config=provider_config,
                params=params,
            )
        else:
            raise ImageGenerationConfigError(f"Unsupported image generation adapter: {adapter_id}")

    return provider_name, metadata, images


def extension_for_mime_type(mime_type: str) -> str:
    if mime_type == "image/jpeg":
        return ".jpg"
    if mime_type == "image/webp":
        return ".webp"
    guessed = mimetypes.guess_extension(mime_type)
    return guessed if guessed in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
