"""Configuration for published-Agent runtime quotas."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from deerflow.publishing.quota import PlatformQuota


class ModelTokenCost(BaseModel):
    """Deployment pricing snapshot in USD per one million tokens."""

    input_usd_per_million_tokens: Decimal = Field(default=Decimal("0"), ge=0)
    output_usd_per_million_tokens: Decimal = Field(default=Decimal("0"), ge=0)


class PublishingConfig(BaseModel):
    """Published-runtime configuration rooted at ``publishing``."""

    platform_quota: PlatformQuota = Field(default_factory=PlatformQuota)
    model_costs: dict[str, ModelTokenCost] = Field(default_factory=dict)


__all__ = ["ModelTokenCost", "PublishingConfig"]
