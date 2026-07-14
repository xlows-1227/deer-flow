"""Configuration for published-Agent runtime quotas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from deerflow.publishing.quota import PlatformQuota


class PublishingConfig(BaseModel):
    """Published-runtime configuration rooted at ``publishing``."""

    platform_quota: PlatformQuota = Field(default_factory=PlatformQuota)


__all__ = ["PublishingConfig"]
