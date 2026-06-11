from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.sandbox.tools import get_thread_data, mask_local_paths_in_output
from deerflow.tools.image_generation import (
    GeneratedImage,
    ImageGenerationConfigError,
    ImageGenerationProviderError,
    extension_for_mime_type,
    generate_images,
)
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)

OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"
_DEFAULT_OUTPUT_SUBDIR = "generated-images"


def _safe_output_subdir(value: str | None) -> str:
    normalized = (value or _DEFAULT_OUTPUT_SUBDIR).replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return _DEFAULT_OUTPUT_SUBDIR
    return "/".join(parts)


def _prompt_slug(prompt: str) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", prompt.lower())
    slug = "-".join(words[:5])
    return slug[:48] or "image"


def _resolve_output_dirs(runtime: Runtime) -> tuple[Path, str]:
    thread_data = get_thread_data(runtime)
    if thread_data is None:
        raise ImageGenerationConfigError("Thread data is not available; cannot save generated images.")
    outputs_path = thread_data.get("outputs_path")
    if not outputs_path:
        raise ImageGenerationConfigError("Thread outputs directory is not available; cannot save generated images.")

    try:
        from deerflow.config.extensions_config import ExtensionsConfig
        from deerflow.tools.image_generation import get_effective_image_generation_config

        output_subdir = get_effective_image_generation_config(ExtensionsConfig.from_file()).output_subdir
    except Exception:
        output_subdir = _DEFAULT_OUTPUT_SUBDIR

    safe_subdir = _safe_output_subdir(output_subdir)
    actual_dir = Path(outputs_path).resolve() / safe_subdir
    actual_dir.mkdir(parents=True, exist_ok=True)
    return actual_dir, f"{OUTPUTS_VIRTUAL_PREFIX}/{safe_subdir}"


def _save_generated_images(runtime: Runtime, prompt: str, images: list[GeneratedImage]) -> list[str]:
    actual_dir, virtual_dir = _resolve_output_dirs(runtime)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    slug = _prompt_slug(prompt)
    paths: list[str] = []

    for index, image in enumerate(images, start=1):
        extension = extension_for_mime_type(image.mime_type)
        filename = f"{timestamp}-{slug}-{index}-{uuid.uuid4().hex[:8]}{extension}"
        target = actual_dir / filename
        target.write_bytes(image.data)
        paths.append(f"{virtual_dir}/{filename}")
    return paths


def _error_command(message: str, tool_call_id: str) -> Command:
    return Command(update={"messages": [ToolMessage(f"Error: {message}", tool_call_id=tool_call_id)]})


@tool("generate_image", parse_docstring=True)
def generate_image_tool(
    runtime: Runtime,
    prompt: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
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
) -> Command:
    """Generate images with the configured image generation provider.

    Use this tool when the user asks you to create, render, or generate a new image.
    If the user has not specified choices that matter for the configured provider
    (for example size, quality, style, negative prompt, or seed), ask a concise
    clarification before calling the tool. Omit provider and model to use the
    defaults selected in Settings > Tools > Image generation.

    Args:
        prompt: Detailed image prompt describing the desired visual result.
        provider: Optional configured provider name. Omit to use the default provider from settings.
        model: Optional provider model. Omit to use the provider's default model from settings.
        size: Optional image size or provider aspect ratio. Use provider-supported values only.
        n: Number of images to generate.
        quality: Optional provider quality value. Use only when the provider supports quality.
        style: Optional provider style or style preset. Use only when the provider supports style.
        moderation: Optional provider moderation setting. Use only when the provider supports moderation.
        background: Optional provider background setting. Use only when the provider supports background.
        negative_prompt: Optional description of what to avoid. Use only when the provider supports negative prompts.
        seed: Optional deterministic seed. Use only when the provider supports seeds.
        extra_params: Optional advanced provider-specific request parameters.
    """
    try:
        provider_name, metadata, images = generate_images(
            prompt=prompt,
            provider=provider,
            model=model,
            size=size,
            n=n,
            quality=quality,
            style=style,
            moderation=moderation,
            background=background,
            negative_prompt=negative_prompt,
            seed=seed,
            extra_params=extra_params,
        )
        virtual_paths = _save_generated_images(runtime, prompt, images)
    except ImageGenerationConfigError as exc:
        logger.warning("generate_image configuration error: %s", exc)
        return _error_command(str(exc), tool_call_id)
    except ImageGenerationProviderError as exc:
        logger.exception("generate_image provider error: %s", exc)
        return _error_command(str(exc), tool_call_id)
    except Exception as exc:  # noqa: BLE001 - tool boundary converts failures to ToolMessage
        logger.exception("generate_image unexpected error")
        thread_data = get_thread_data(runtime)
        return _error_command(mask_local_paths_in_output(f"{type(exc).__name__}: {exc}", thread_data), tool_call_id)

    files = "\n".join(f"- {path}" for path in virtual_paths)
    return Command(
        update={
            "artifacts": virtual_paths,
            "messages": [
                ToolMessage(
                    f"Generated {len(virtual_paths)} image(s) with {metadata.display_name} provider '{provider_name}'.\n{files}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
