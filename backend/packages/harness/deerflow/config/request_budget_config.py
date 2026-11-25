"""Configuration for outbound LLM request size budgeting."""

from pydantic import BaseModel, Field, model_validator


class RequestBudgetConfig(BaseModel):
    """Bound the serialized size of an outbound model request.

    When the estimated request size exceeds ``max_request_chars``, the longest
    tool results are replaced with a head + tail preview plus an omission
    marker. This only affects the current model call; conversation state is
    left untouched.

    Scope: only ``ToolMessage`` results are trimmed. Oversized user input,
    system prompts and tool schemas are counted but cannot be shortened, so
    this is a tool-output safety net rather than a guarantee that every
    request fits the model's context window.
    """

    enabled: bool = Field(
        default=True,
        description="Whether to trim oversized tool results before each model call.",
    )
    max_request_chars: int = Field(
        default=800000,
        ge=10000,
        description=(
            "Estimated serialized characters allowed for one model request. "
            "Pure Chinese text can reach 1 char per token in the worst case "
            "(mixed Chinese/English averages ~1.4), so the default is sized "
            "against that floor: 800,000 chars leaves headroom below the "
            "deployment limit of 983,616 tokens even with tokenizer overhead."
        ),
    )
    keep_head_chars: int = Field(
        default=2000,
        ge=0,
        description="Characters kept at the start of a trimmed tool result.",
    )
    keep_tail_chars: int = Field(
        default=2000,
        ge=0,
        description="Characters kept at the end of a trimmed tool result.",
    )

    @model_validator(mode="after")
    def _validate_keep_window(self) -> "RequestBudgetConfig":
        if self.keep_head_chars + self.keep_tail_chars < 200:
            raise ValueError("keep_head_chars + keep_tail_chars must be at least 200")
        return self
