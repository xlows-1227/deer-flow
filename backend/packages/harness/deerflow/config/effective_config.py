from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from deerflow.config.app_config import AppConfig, get_app_config, pop_current_app_config, push_current_app_config
from deerflow.config.model_config import ModelConfig

logger = logging.getLogger(__name__)

_USER_MODEL_CACHE_TTL_SECONDS = 30.0
_user_model_cache: dict[str, tuple[float, list[ModelConfig]]] = {}
_user_model_cache_lock = asyncio.Lock()


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


async def build_effective_app_config(*, user_id: str | None = None) -> AppConfig:
    base = get_app_config()
    if not user_id:
        return base
    user_models = await load_user_model_configs(user_id)
    return merge_model_configs(base, user_models)


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
