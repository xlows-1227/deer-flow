from pydantic import BaseModel, Field, field_validator

DEFAULT_SECURITY_SCAN_CONCURRENCY = 3
MAX_SECURITY_SCAN_CONCURRENCY = 32


class SkillEvolutionConfig(BaseModel):
    """Configuration for agent-managed skill evolution."""

    enabled: bool = Field(
        default=False,
        description="Whether the agent can create and modify skills under skills/custom.",
    )
    moderation_model_name: str | None = Field(
        default=None,
        description="Optional model name for skill security moderation. Defaults to the primary chat model.",
    )
    security_scan_concurrency: int = Field(
        default=DEFAULT_SECURITY_SCAN_CONCURRENCY,
        description=(
            "Maximum number of files scanned concurrently by the skill security scanner. "
            f"Clamped to 1..{MAX_SECURITY_SCAN_CONCURRENCY}."
        ),
    )
    security_rescan_enabled: bool = Field(
        default=True,
        description="Whether the background loop rescans skills whose security status is 'unscanned'.",
    )
    security_rescan_interval_seconds: int = Field(
        default=300,
        description="Minimum seconds between two background security rescan batches.",
    )
    security_rescan_batch_size: int = Field(
        default=5,
        description="Maximum number of unscanned skills processed per background batch.",
    )
    security_rescan_concurrency: int = Field(
        default=1,
        description="Maximum number of skills scanned concurrently per background batch.",
    )

    @field_validator("security_scan_concurrency", mode="before")
    @classmethod
    def _clamp_security_scan_concurrency(cls, value: object) -> int:
        """Clamp invalid or out-of-range values instead of failing config loading."""
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return DEFAULT_SECURITY_SCAN_CONCURRENCY
        return max(1, min(parsed, MAX_SECURITY_SCAN_CONCURRENCY))
