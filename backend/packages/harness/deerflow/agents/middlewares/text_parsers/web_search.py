"""Parsers for web_search text-embedded tool calls.

Some LLM providers (e.g. YUMC) return ``web_search`` as text content instead
of in the standard ``tool_calls`` response field. This module provides patterns
to detect and convert these text-embedded calls.
"""

from __future__ import annotations

from .base import TextToolCallParser

PARSERS: list[TextToolCallParser] = [
    # Format 1: Standard function-call style
    #   web_search(query="北京今日天气", max_results=5)
    TextToolCallParser(
        tool_name="web_search",
        pattern=r"web_search\s*\(\s*(.+?)\s*\)",
        description="web_search returned as text (parenthesized args)",
    ),
    # Format 2: Assignment-style (YUMC specific)
    #   ={"query": "北京今日天气", "max_results": 5}
    # The = prefix indicates the model is assigning parameters for a tool call.
    # We match ={ followed by "query" to reduce false positives.
    TextToolCallParser(
        tool_name="web_search",
        pattern=r'=\s*(\{\s*"query"\s*:\s*.+?\})',
        description="web_search as =-prefixed JSON assignment (YUMC style)",
    ),
]
