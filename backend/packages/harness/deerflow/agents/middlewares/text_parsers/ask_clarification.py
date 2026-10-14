"""Parsers for ask_clarification text-embedded tool calls.

Some LLM providers return ``ask_clarification`` as text content instead of
in the standard ``tool_calls`` response field. This module provides patterns
to detect and convert these text-embedded calls.
"""

from __future__ import annotations

from .base import TextToolCallParser

# Field labels for multi-capture-group (YAML/Markdown) format
_CLARIFICATION_LABELS = ["question", "clarification_type", "context", "options"]

PARSERS: list[TextToolCallParser] = [
    # Format 1: Python kwargs or JSON dict
    #   ask_clarification({"question": "...", "options": [...]})
    #   ask_clarification(question="...", options=[...])
    TextToolCallParser(
        tool_name="ask_clarification",
        pattern=r"ask_clarification\s*\(\s*(.+?)\s*\)",
        description="ask_clarification returned as text (parenthesized args)",
    ),
    # Format 2: YAML/Markdown-style with flexible case labels
    #   ask_clarification
    #   question: ...
    #   clarification_type: ...
    #   context: ...
    #   options: ["北京", "上海"]  OR  multi-line list
    TextToolCallParser(
        tool_name="ask_clarification",
        pattern=(
            r"ask_clarification\s*\n"
            r"(?:[Qq]uestion\s*[:：]\s*(.+?)\s*\n)?"
            r"(?:[Cc]larification_?[Tt]ype\s*[:：]\s*(.+?)\s*\n)?"
            r"(?:[Cc]ontext\s*[:：]\s*(.+?)\s*\n)?"
            r"(?:[Oo]ptions\s*[:：]\s*(.+?)\s*(?:\n|$))?"
        ),
        description="ask_clarification as YAML/Markdown text block (flexible labels)",
        labels=_CLARIFICATION_LABELS,
    ),
]
