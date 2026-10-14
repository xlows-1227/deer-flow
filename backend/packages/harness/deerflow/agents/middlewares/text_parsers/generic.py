"""Generic fallback parser for text-embedded tool calls.

Matches ``any_tool_name(args)`` format where the tool name is extracted
from the match itself. This is used as a last-resort fallback when
tool-specific parsers don't match.

Known tool names are matched to reduce false positives. The list is
populated at import time from the tool definitions in the system.
"""

from __future__ import annotations

import re
from typing import Any

from .base import TextToolCallParser, parse_json_or_kwargs

# Tool names known to the system. When the generic parser detects
# ``some_name(args)``, it only converts if ``some_name`` is in this set.
# This prevents false positives on ordinary text like "print(hello)".
_KNOWN_TOOL_NAMES: set[str] = {
    "web_search",
    "ask_clarification",
    "view_result",
    "math_calculator",
    "image_search",
    "task",
    "view_image",
    "skill_manager",
}

# Pattern: tool_name(args) where args can be anything within balanced parens.
# Capture group 1 = tool name, group 2 = raw arguments.
_GENERIC_PATTERN = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s*\(\s*(.+?)\s*\)",
    re.DOTALL | re.IGNORECASE,
)


class GenericTextToolCallParser(TextToolCallParser):
    """Fallback parser that extracts the tool name from the match.

    Only matches if the extracted tool name is in ``_KNOWN_TOOL_NAMES``.
    """

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            tool_name="",
            pattern=_GENERIC_PATTERN,
            description="generic tool_name(args) fallback",
            tool_name_group=1,
        )

    def get_tool_name(self, match: re.Match[str]) -> str:
        name = match.group(1)
        return name if name in _KNOWN_TOOL_NAMES else ""

    def parse(self, match: re.Match[str]) -> dict[str, Any]:
        # Group 2 contains the raw arguments
        raw = match.group(2).strip()
        return parse_json_or_kwargs(raw)


PARSERS: list[TextToolCallParser] = [GenericTextToolCallParser()]
