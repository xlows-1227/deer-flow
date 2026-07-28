"""Tests for AGENT.md + SOUL.md instruction composition.

Locks the F1.6 contract from design doc §3.3: AGENT.md always precedes SOUL.md,
each non-empty file is wrapped in a labelled block, empty files are skipped,
and an all-empty input is an error.
"""

from __future__ import annotations

import pytest

from deerflow.publishing.instructions import compose_agent_instructions


def test_only_agent_markdown():
    out = compose_agent_instructions(agent_markdown="# You are a helper", soul_markdown="")
    assert "<agent_instructions>" in out
    assert "<agent_soul>" not in out
    assert "You are a helper" in out


def test_only_soul_markdown():
    out = compose_agent_instructions(agent_markdown="", soul_markdown="# Friendly tone")
    assert "<agent_soul>" in out
    assert "<agent_instructions>" not in out
    assert "Friendly tone" in out


def test_both_markdowns_and_order():
    out = compose_agent_instructions(agent_markdown="# Agent", soul_markdown="# Soul")
    assert "<agent_instructions>" in out
    assert "<agent_soul>" in out
    # AGENT block must come before SOUL block.
    assert out.index("<agent_instructions>") < out.index("<agent_soul>")


def test_whitespace_only_is_treated_as_empty():
    out = compose_agent_instructions(agent_markdown="   \n\n  ", soul_markdown="# Soul")
    assert "<agent_instructions>" not in out
    assert "<agent_soul>" in out


def test_all_empty_raises():
    with pytest.raises(ValueError):
        compose_agent_instructions(agent_markdown="", soul_markdown="")
    with pytest.raises(ValueError):
        compose_agent_instructions(agent_markdown="   ", soul_markdown="\n")


def test_blocks_are_double_newline_separated():
    out = compose_agent_instructions(agent_markdown="# A", soul_markdown="# S")
    assert "</agent_instructions>\n\n<agent_soul>" in out
