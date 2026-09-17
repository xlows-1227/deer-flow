"""Regression tests for English space restoration (worker._restore_english_spaces).

The function runs on English-like model output at BOTH write time
(``_clean_model_text``) and read time (``message_patch`` / agent
serialization), so it must be idempotent.  Earlier versions were not:

* every pass appended one extra space after contraction suffixes
  ("Here's some" -> "Here's  some" -> "Here's   some");
* an opening quote attached to its word ('"Hello') gained a space on
  every pass ('" Hello');
* a closing quote glued to sentence punctuation ('there!" she') was
  split apart.

These tests pin down: clean text round-trips unchanged, legacy damage
already stored by the old versions is repaired, glued model output is
still repaired, and protected/structural content (code blocks, markdown
hard line breaks, paragraph breaks) is left alone.
"""

from __future__ import annotations

import pytest

from deerflow.runtime.runs.worker import _restore_english_spaces

# (input, expected) — clean English must round-trip unchanged.
ROUND_TRIP_CASES = [
    (
        "Hello there! The sun rises in the east and sets in the west.",
        "Hello there! The sun rises in the east and sets in the west.",
    ),
    (
        '"Hello there!" she said.',
        '"Hello there!" she said.',
    ),
    (
        "Here's some English and it's fine; you'd like it, we'll see.",
        "Here's some English and it's fine; you'd like it, we'll see.",
    ),
]

# (input, expected) — damage stored by earlier versions must be repaired
# on read so old threads render correctly again.
LEGACY_REPAIR_CASES = [
    ("Here's   some English", "Here's some English"),
    ("it's   a pleasure", "it's a pleasure"),
    ("you'd   like", "you'd like"),
    ('" Hello there!', '"Hello there!'),
    ('He said " Hello there!" and left.', 'He said "Hello there!" and left.'),
]

# (input, expected) — glued model output must still be repaired.
GLUED_CASES = [
    ("Itseemsthemessage", "It seems the message"),
    ("I'lladjustforyou", "I'll adjust for you"),
    ("choose.Even small", "choose. Even small"),
    ("comfortable,but", "comfortable, but"),
    ("general?Letmeknow", "general? Let me know"),
    ("Sure!Here", "Sure! Here"),
    ('meaningful."Anda', 'meaningful." And a'),
    ('you:"Everyday is fine', 'you: "Everyday is fine'),
    ('end">What', 'end"> What'),
    ('said"Hello there', 'said "Hello there'),
]

# (input, expected) — protected / structural content must be untouched.
PROTECTED_CASES = [
    (
        '```python\nx = " Hello"\ny = "it\'s  ok"\n```',
        '```python\nx = " Hello"\ny = "it\'s  ok"\n```',
    ),
    # Two trailing spaces before a newline are a markdown hard break.
    ("line one  \nline two", "line one  \nline two"),
    ("that's it\n\nSome new paragraph", "that's it\n\nSome new paragraph"),
]


@pytest.mark.parametrize(("source", "expected"), ROUND_TRIP_CASES)
def test_clean_text_round_trips(source: str, expected: str) -> None:
    assert _restore_english_spaces(source) == expected


@pytest.mark.parametrize(("source", "expected"), LEGACY_REPAIR_CASES)
def test_legacy_over_spacing_is_repaired(source: str, expected: str) -> None:
    assert _restore_english_spaces(source) == expected


@pytest.mark.parametrize(("source", "expected"), GLUED_CASES)
def test_glued_output_is_still_repaired(source: str, expected: str) -> None:
    assert _restore_english_spaces(source) == expected


@pytest.mark.parametrize(("source", "expected"), PROTECTED_CASES)
def test_protected_content_is_untouched(source: str, expected: str) -> None:
    assert _restore_english_spaces(source) == expected


def test_idempotent_across_repeated_passes() -> None:
    # The restore pass runs at write time and again at read time; applying
    # it repeatedly must never add or remove spacing.
    sample = "Here's some English and \"Hello there!\" it's fine."
    once = _restore_english_spaces(sample)
    assert once == sample
    assert _restore_english_spaces(once) == once
    assert _restore_english_spaces(_restore_english_spaces(once)) == once


def test_user_reported_sample_is_repaired() -> None:
    damaged = '" Hello there! The sun rises in the east and sets in the west, and every day brings a fresh chance to learn something new. Language is a bridge between minds — and it\'s   a pleasure to cross that bridge with you today."'
    repaired = _restore_english_spaces(damaged)
    assert repaired == (
        '"Hello there! The sun rises in the east and sets in the west, and every day brings a fresh chance to learn something new. Language is a bridge between minds — and it\'s a pleasure to cross that bridge with you today."'
    )
    assert _restore_english_spaces(repaired) == repaired
