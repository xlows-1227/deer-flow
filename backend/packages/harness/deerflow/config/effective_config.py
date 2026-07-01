from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager

from deerflow.config.app_config import AppConfig, get_app_config, pop_current_app_config, push_current_app_config
from deerflow.config.extensions_config import ExtensionsConfig, get_extensions_config
from deerflow.config.model_config import ModelConfig

logger = logging.getLogger(__name__)

_USER_MODEL_CACHE_TTL_SECONDS = 30.0
_USER_EXTENSIONS_CACHE_TTL_SECONDS = 30.0
_user_model_cache: dict[str, tuple[float, list[ModelConfig]]] = {}
_user_extensions_cache: dict[str, tuple[float, ExtensionsConfig]] = {}
_user_model_cache_lock = asyncio.Lock()
_user_extensions_cache_lock = asyncio.Lock()


def merge_model_configs(base: AppConfig, user_models: list[ModelConfig]) -> AppConfig:
    """Merge global config models with per-user custom models.

    User models with the same ``name`` override global entries.
    """
    if not user_models:
        return base

    merged = list(base.models)
    index_by_name = {model.name: idx for idx, model in enumerate(merged)}
    for user_model in user_models:
        if user_model.name in index_by_name:
            merged[index_by_name[user_model.name]] = user_model
        else:
            merged.append(user_model)
            index_by_name[user_model.name] = len(merged) - 1
    return base.model_copy(update={"models": merged})


def invalidate_user_model_cache(user_id: str) -> None:
    _user_model_cache.pop(user_id, None)
    invalidate_user_extensions_cache(user_id)


def invalidate_user_extensions_cache(user_id: str) -> None:
    _user_extensions_cache.pop(user_id, None)
    try:
        from deerflow.mcp.cache import invalidate_mcp_tools_cache_for_user

        invalidate_mcp_tools_cache_for_user(user_id)
    except Exception:
        logger.debug("Could not invalidate MCP cache for user %s", user_id, exc_info=True)


async def load_user_model_configs(user_id: str) -> list[ModelConfig]:
    now = time.monotonic()
    cached = _user_model_cache.get(user_id)
    if cached is not None and now - cached[0] < _USER_MODEL_CACHE_TTL_SECONDS:
        return cached[1]

    async with _user_model_cache_lock:
        cached = _user_model_cache.get(user_id)
        if cached is not None and now - cached[0] < _USER_MODEL_CACHE_TTL_SECONDS:
            return cached[1]

        configs: list[ModelConfig] = []
        try:
            from deerflow.user_models.service import make_user_model_service

            service = make_user_model_service()
            configs = await service.list_model_configs(user_id)
        except Exception as exc:
            logger.debug("User model configs unavailable for %s: %s", user_id, exc)

        _user_model_cache[user_id] = (time.monotonic(), configs)
        return configs


async def load_user_extensions_config(user_id: str) -> ExtensionsConfig:
    now = time.monotonic()
    cached = _user_extensions_cache.get(user_id)
    if cached is not None and now - cached[0] < _USER_EXTENSIONS_CACHE_TTL_SECONDS:
        return cached[1]

    async with _user_extensions_cache_lock:
        cached = _user_extensions_cache.get(user_id)
        if cached is not None and now - cached[0] < _USER_EXTENSIONS_CACHE_TTL_SECONDS:
            return cached[1]

        base = get_extensions_config()
        merged = await build_effective_extensions_config(user_id, base=base)
        _user_extensions_cache[user_id] = (time.monotonic(), merged)
        return merged


async def build_effective_extensions_config(user_id: str, *, base: ExtensionsConfig | None = None) -> ExtensionsConfig:
    base = base or get_extensions_config()
    try:
        from deerflow.extensions_user.image_service import make_user_image_service
        from deerflow.extensions_user.mcp_service import make_user_mcp_service

        mcp_service = make_user_mcp_service()
        image_service = make_user_image_service()
        mcp_servers = await mcp_service.build_user_mcp_servers(user_id, base.mcp_servers)
        image_generation = await image_service.build_user_image_config(user_id)
    except Exception as exc:
        logger.debug("Per-user extensions unavailable for %s: %s", user_id, exc)
        return base.model_copy(
            update={
                "mcp_servers": {},
                "image_generation": base.image_generation.model_copy(update={"enabled": False}),
            }
        )

    return base.model_copy(
        update={
            "mcp_servers": mcp_servers,
            "image_generation": image_generation,
        }
    )


def extensions_config_fingerprint(config: ExtensionsConfig) -> str:
    payload = config.model_dump(mode="json", by_alias=True)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def build_effective_app_config(*, user_id: str | None = None) -> AppConfig:
    base = get_app_config()
    if not user_id:
        return base
    user_models = await load_user_model_configs(user_id)
    merged = merge_model_configs(base, user_models)
    extensions = await load_user_extensions_config(user_id)
    return merged.model_copy(update={"extensions": extensions})


@asynccontextmanager
async def effective_app_config_scope(user_id: str | None):
    """Push merged AppConfig for the given user for the duration of the block."""
    if not user_id:
        yield
        return
    merged = await build_effective_app_config(user_id=user_id)
    push_current_app_config(merged)
    try:
        yield merged
    finally:
        pop_current_app_config()


def reset_user_model_cache_for_tests() -> None:
    _user_model_cache.clear()
    _user_extensions_cache.clear()
