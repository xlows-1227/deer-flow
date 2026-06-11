"""Configuration for External API authentication."""

from __future__ import annotations

import logging
import os
import secrets

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
_PEPPER_FILE = ".external_api_key_pepper"


class ExternalAPIConfig(BaseModel):
    api_key_pepper: str = Field(min_length=32)
    active_run_limit_per_user: int = Field(default=3, ge=1, le=100)


_config: ExternalAPIConfig | None = None


def _load_or_create_pepper() -> str:
    from deerflow.config.paths import get_paths

    pepper_file = get_paths().base_dir / _PEPPER_FILE
    try:
        if pepper_file.exists():
            pepper = pepper_file.read_text(encoding="utf-8").strip()
            if pepper:
                return pepper
    except OSError as exc:
        raise RuntimeError(f"Failed to read External API Key Pepper from {pepper_file}. Set EXTERNAL_API_KEY_PEPPER or fix DEER_FLOW_HOME permissions.") from exc

    pepper = secrets.token_urlsafe(48)
    try:
        pepper_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(pepper_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(pepper)
    except OSError as exc:
        raise RuntimeError(f"Failed to persist External API Key Pepper to {pepper_file}. Set EXTERNAL_API_KEY_PEPPER or fix DEER_FLOW_HOME permissions.") from exc
    return pepper


def get_external_api_config() -> ExternalAPIConfig:
    global _config
    if _config is None:
        pepper = os.environ.get("EXTERNAL_API_KEY_PEPPER") or _load_or_create_pepper()
        if "EXTERNAL_API_KEY_PEPPER" not in os.environ:
            logger.warning("EXTERNAL_API_KEY_PEPPER is not set; using a persisted development Pepper.")
        _config = ExternalAPIConfig(api_key_pepper=pepper)
    return _config


def set_external_api_config(config: ExternalAPIConfig | None) -> None:
    global _config
    _config = config
