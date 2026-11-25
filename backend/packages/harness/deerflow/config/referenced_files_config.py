"""Configuration for files referenced through the ``@`` picker.

Only oversized references are affected. Files at or below
``inline_max_bytes`` keep the legacy inline behaviour byte for byte.
"""

from pydantic import BaseModel, ConfigDict, Field


class ReferencedFilesConfig(BaseModel):
    inline_max_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=100_000,
        le=100 * 1024 * 1024,
        description=(
            "Files larger than this many bytes are no longer inlined when referenced with '@'. "
            "Thread uploads get their sandbox path only; library files are first copied into the "
            "thread workspace. Default 4 MiB."
        ),
    )
    max_copy_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1_000_000,
        le=100 * 1024 * 1024,
        description=(
            "Upper bound for copying an oversized library file into the thread workspace. "
            "Files above this limit fall back to the legacy truncated inline copy."
        ),
    )
    copy_retention_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description="Age in hours after which copies under workspace/referenced/ are pruned.",
    )

    model_config = ConfigDict(extra="allow")
