"""Text-embedded tool call parser registry.

Aggregates per-tool parsers into a single ``ALL_PARSERS`` list.
The middleware loads this list by default.

Parser order matters: tool-specific parsers are tried first, the generic
fallback is last. This ensures specific patterns take priority over the
generic ``tool_name(args)`` matcher.
"""

from __future__ import annotations

from .base import TextToolCallParser
from .ask_clarification import PARSERS as _ask_clarification_parsers
from .dsml import PARSERS as _dsml_parsers
from .web_search import PARSERS as _web_search_parsers
from .generic import PARSERS as _generic_parsers

ALL_PARSERS: list[TextToolCallParser] = [
    # Tool-specific parsers first (higher priority)
    *_ask_clarification_parsers,
    *_web_search_parsers,
    *_dsml_parsers,
    # Generic fallback last
    *_generic_parsers,
]

__all__ = ["TextToolCallParser", "ALL_PARSERS"]
