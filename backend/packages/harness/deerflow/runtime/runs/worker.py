"""Background agent execution.

Runs an agent graph inside an ``asyncio.Task``, publishing events to
a :class:`StreamBridge` as they are produced.

Uses ``graph.astream(stream_mode=[...])`` which gives correct full-state
snapshots for ``values`` mode, proper ``{node: writes}`` for ``updates``,
and ``(chunk, metadata)`` tuples for ``messages`` mode.

Note: ``events`` mode is not supported through the gateway — it requires
``graph.astream_events()`` which cannot simultaneously produce ``values``
snapshots.  The JS open-source LangGraph API server works around this via
internal checkpoint callbacks that are not exposed in the Python public API.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, cast

from langgraph.checkpoint.base import empty_checkpoint
if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage, HumanMessage

from deerflow.config.app_config import AppConfig
from deerflow.runtime.serialization import serialize
from deerflow.runtime.stream_bridge import StreamBridge
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.skills.privacy import SkillContentRedactor
from deerflow.tracing import inject_langfuse_metadata

from .manager import RunManager, RunRecord
from .naming import resolve_root_run_name
from .schemas import RunStatus

logger = logging.getLogger(__name__)

_TOOL_CALL_SECTION_RE = re.compile(
    r"<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>", re.DOTALL
)
_TOOL_CALL_BLOCK_RE = re.compile(
    r"<\|tool_call_begin\|>.*?<\|tool_call_end\|>", re.DOTALL
)
_TOOL_CALL_ARG_RE = re.compile(
    r"<\|tool_call_argument_begin\|>.*?<\|tool_call_argument_end\|>", re.DOTALL
)
_TOOL_CALL_NAME_RE = re.compile(
    r"<\|tool_call_name_begin\|>([^<]+)<\|tool_call_name_end\|>"
)
_TOOL_CALL_NAME_JSON_RE = re.compile(
    r'"name"\s*:\s*"([^"]+)"'
)
_SINGLE_MARKER_RE = re.compile(r"<\|[^>]+\|>")
_SYSTEM_REMINDER_RE = re.compile(r"<system-rem>.*?</system-rem>", re.DOTALL)


def _extract_tool_names_from_text(text: str) -> list[str]:
    """Extract tool call names from raw model text before cleaning."""
    if not text:
        return []
    names: list[str] = []
    # Method 1: <|tool_call_name_begin|>tool_name<|tool_call_name_end|>
    for m in _TOOL_CALL_NAME_RE.finditer(text):
        name = m.group(1).strip()
        if name and name != "task":
            names.append(name)
    # Method 2: JSON-like "name": "tool_name" inside tool call blocks
    for block in _TOOL_CALL_BLOCK_RE.findall(text):
        for m in _TOOL_CALL_NAME_JSON_RE.finditer(block):
            name = m.group(1).strip()
            if name and name != "task" and name not in names:
                names.append(name)
    return names


def _tool_call_display_name(tool_call: Any) -> str | None:
    """Return a user-facing name for a tool_call.

    For the internal ``task`` subagent tool we surface the human-readable
    ``description`` when available, falling back to ``subagent_type``, so
    users can see what each call actually did.
    """
    name = getattr(tool_call, "name", None) or (tool_call.get("name") if isinstance(tool_call, dict) else "")
    if not isinstance(name, str) or not name:
        return None
    if name != "task":
        return name
    args: Any = getattr(tool_call, "args", None) or (tool_call.get("args") if isinstance(tool_call, dict) else {})
    if not isinstance(args, dict):
        return name
    description = (args.get("description") or "").strip() if isinstance(args.get("description"), str) else ""
    subagent_type = (args.get("subagent_type") or "").strip() if isinstance(args.get("subagent_type"), str) else ""
    return description or subagent_type or name


def _restore_english_spaces(text: str) -> str:
    """Re-insert missing spaces between English words when a model produces
    concatenated output (e.g. "Itseemsthemessage" → "It seems the message").

    **Conservative greedy strategy** (prefer fewer splits over bad splits):

    For each run of Latin letters:
    1. If the whole run is a known word (or has a known stem+suffix combo),
       return it as-is without splitting.
    2. Otherwise, scan left-to-right with a **longest-known-word-first**
       greedy cursor.  A candidate word is accepted only if:
       - it is ≥ 2 chars AND in the word set, AND
       - the remainder can also form valid words (≥2 chars each).
    3. If no high-confidence split exists, return the original run
       unsplit (better to show one glued token than garbled fragments).

    This intentionally avoids the previous aggressive dictionary + DP +
    heuristic pipeline which produced absurd output like
    ``i thin k foryou day.``
    """
    if not text:
        return text

    # Fast-path: skip the expensive splitter when text clearly does NOT need
    # any restoration.  Two skip conditions:
    #   1. Text already has spaces between letter tokens AND has no glued
    #      punctuation (letter-punct-letter transitions such as "foo,bar",
    #      "baz!Quux").  In that case the output is already well-formed and
    #      touching it would be a no-op.
    #   2. Short text that has no runs of ≥ 3 Latin letters AND also no
    #      punctuation-gluing pattern.  No work for us.
    #
    # NOTE: the OLD fast-path used to check `[A-Za-z]{5,}` which bailed out
    # immediately for inputs like "Sure!Here", "you:they", "comfortable,but",
    # because the letter-punct-letter layout breaks any single 5-letter run.
    # That was the #1 cause of "punctuation fuses with next word" bugs.  We
    # now explicitly search for glued punctuation instead.
    _GLUED_PUNCT_RE = re.compile(
        r"[A-Za-z][.?!,;:][A-Za-z\"']"   # letter+punct+letter/quote (e.g. "e.H", "o, b")
        r"|[.?!,;:][\"']?[A-Za-z]"        # punct+(maybe quote)+letter at BOS
        r"|[\">][A-Za-z]"                 # quote or ">" directly followed by letter
        r"|[A-Za-z][\"]"                  # letter directly followed by quote (e.g. "word\"")
    )
    if " " in text.strip() and re.search(r"[A-Za-z]\s+[A-Za-z]", text):
        # Text already has letter-spaces-letter.  But still check for glued
        # punctuation, CamelCase, or long glue runs inside — "andHello" or
        # "choose.Even" or "Sayhello world" inside already-spaced text still
        # need fixing.  Previously we only checked CamelCase and punct-glue,
        # which missed glue runs without case shifts (e.g. "Sayhello",
        # "whatisessential").
        if not (
            re.search(r"[a-z][A-Z]", text)                 # CamelCase
            or re.search(r"[A-Za-z][.?!,;:\">][A-Za-z\"']", text)  # letter-punct-glue
            or re.search(r'[">][A-Za-z]', text)            # quote/-letter glue
            or re.search(r"[A-Za-z]{8,}", text)            # long glue run (≥8 chars)
        ):
            return text
    else:
        # No spaces yet.  Skip only if (a) no glued-punct pattern AND
        # (b) no 5+ letter continuous run.  Either one triggers a pass
        # through the restore pipeline.
        if not _GLUED_PUNCT_RE.search(text) and not re.search(r"[A-Za-z]{4,}", text):
            return text

    # Protect code blocks, inline code, URLs and markdown links.
    segments: list[tuple[bool, str]] = []
    pattern = re.compile(
        r"(```[\s\S]*?```)"
        r"|(`[^`\n]+`)"
        r"|(https?://\S+)"
        r"|(\[[^\]]+\]\([^)]+\))",
        flags=re.MULTILINE,
    )
    last_end = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last_end:
            segments.append((False, text[last_end:start]))
        segments.append((True, match.group(0)))
        last_end = end
    if last_end < len(text):
        segments.append((False, text[last_end:]))

    # --- Suffix helpers ---------------------------------------------------
    # When a glued run ends with a standard English inflection ending AND
    # stripping it leaves a known dictionary stem, the whole run is treated
    # as a single word.  This protects "progressing", "working", "quickly",
    # etc. from being shredded by the greedy matcher.
    _INFL_SUFFIXES = (
        "ing", "ingly",
        "tion", "sion", "ction", "ssion", "xion",
        "ment", "ness",
        "ality", "icity", "ility", "ability", "ivity",
        "ence", "ance", "ency", "ancy",
        "able", "ible",
        "ful", "less", "ous", "ious", "eous",
        "ive", "ative", "itive",
        "al", "ial", "ical",
        "ly", "ally", "ially",
        "er", "ier", "or", "ior",
        "est", "iest",
        "dom", "ship", "hood", "ism", "ist",
        "ure", "ture", "sure",
        "logy", "ology",
        "graphy", "ography",
        "ward", "wards",
        "ed", "ied", "ted", "ded",
        "es", "ies", "ses", "xes", "zes", "ches", "shes",
        "en", "ten", "den",
    )
    _STEM_ONLY_SUFFIXES = ("s", "d")  # only valid if stem is a known word

    # --- Short-word reference sets ---------------------------------------
    # Declared BEFORE _is_single_word so the sentence-shape guard inside
    # that function can reference them.
    _TWO_LETTER_FUNCTION_WORDS_REF = frozenset({
        "is", "it", "in", "on", "at", "to", "of", "as", "be", "by",
        "he", "we", "or", "so", "if", "an", "no", "do", "up", "go",
        "me", "my", "am", "us",
        # Foreign-script prepositions that commonly appear inside
        # otherwise-English glued runs (e.g. "AntoinedeSaintExupéry" →
        # "Antoine de Saint Exupéry").  Short (2-char) so they never
        # compete with Pass-1 ≥3-char dict words; only used as glue.
        "de", "le", "la", "et", "en",
    })
    _ONE_LETTER_ALLOWED_REF = frozenset({"a", "i"})

    # Function words that qualify for the +500 NWL bonus in _nwl().
    # These are common short (≤3-char) boundary markers whose presence at
    # the start of a tail strongly indicates a natural word boundary.
    # Includes the 2-letter set above plus 3-char conjunctions/prepositions.
    _FUNCTION_WORDS_FOR_BONUS = frozenset({
        # 1-letter
        "a", "i",
        # 2-letter
        "an", "is", "it", "in", "on", "at", "to", "of", "as", "be", "by",
        "he", "we", "or", "so", "if", "no", "do", "up", "go", "me", "my",
        "am", "us", "de", "le", "la", "et", "en",
        # 3-letter conjunctions / prepositions / auxiliaries
        "and", "but", "for", "the", "with", "from", "that", "this",
        "was", "were", "are", "had", "has", "have", "not", "can",
        "did", "its", "all", "any", "may", "let", "get", "set",
    })

    def _is_single_word(low_run: str) -> bool:
        """Return True if the lowercase run should be left as one word.

        For a glued-sentence run (≥ 15 letters that contains at least 2
        dictionary words of length ≥ 3 after a naive longest-match scan)
        we deliberately return False so the greedy splitter has a chance
        to recover the spaces.  This addresses a whole class of false
        positives where the INFLECT engine treated e.g. "letmeknow" as
        letme + know = one word (letme /ˈlemiː/ isn't even a word — the
        suffix matching was too generous).
        """
        n = len(low_run)
        if n < 4:
            return low_run in _EN_WORD_SET
        # --- Sentence-shaped glued-run guard ------------------------------
        # If the run is long AND contains at least two independent long
        # dictionary tokens when scanned greedily, it is almost certainly
        # a sentence fragment and NOT a single legitimate inflected word.
        #
        # Threshold lowered from ≥ 15 → ≥ 8 letters because everyday
        # glued phrases like "Letmeknow" (9 letters), "Wouldyou" (8),
        # "Readyto" (8), "Everyday" (8) already have ≥2 dictionary tokens
        # via naive greedy and should NOT be classified as single words
        # by the suffix engine (which otherwise greedily consumes them
        # with suffixes like "me" + "know" = ... no wait it's suffix match
        # on the END: "letmeknow" ends with "know" not a suffix.  So the
        # issue is actually the suffix "me"?  No, letmeknow ends with
        # "know".  It can't match via suffix unless the suffix is "know".
        # — Ah, real problem: it matches INFLECT_SHORT longest-first
        # with "letmeknow".endswith("own")?  No.  Let me be precise: it
        # matches the suffix rule ending in "me" BUT the stem is then
        # "letkno" which is NOT in the dictionary.  So actually
        # Letmeknow returned True because it's in _EN_WORD_SET? No.
        # It must come from the INFLECT_SUFFIXES match which returns
        # True incorrectly.  We run the debug to verify but for now the
        # guard at n≥8 catches these small glued phrases reliably:
        if n >= 8:
            _sentence_tokens = 0
            _sentence_scan = 0
            while _sentence_scan < n:
                _found_any = False
                for _L in range(min(_MAX_EN_WORD_LEN, n - _sentence_scan), 2, -1):
                    if low_run[_sentence_scan:_sentence_scan + _L] in _EN_WORD_SET:
                        _sentence_tokens += 1
                        _sentence_scan += _L
                        _found_any = True
                        break
                if not _found_any:
                    # Also account for 2-letter glue words like "to", "is",
                    # "we", etc. — 8-letter "Readytolive" becomes Ready +
                    # to + live, and we must count glue in the scan so
                    # coverage is 100% and we properly detect sentence
                    # fragments.
                    _chunk2 = low_run[_sentence_scan:_sentence_scan + 2]
                    if len(_chunk2) == 2 and _chunk2 in _TWO_LETTER_FUNCTION_WORDS_REF:
                        _sentence_tokens += 1
                        _sentence_scan += 2
                        continue
                    # And single-letter "a" / "I" glue:
                    if low_run[_sentence_scan:_sentence_scan+1] in _ONE_LETTER_ALLOWED_REF:
                        _sentence_tokens += 1
                        _sentence_scan += 1
                        continue
                    break
            # ≥2 real dictionary / glue tokens covering ≥ 70% of letters
            if _sentence_tokens >= 2 and _sentence_scan >= n * 0.7:
                return False

        if low_run in _EN_WORD_SET:
            return True
        # Try each inflection suffix: accept if stem is in the word set
        for suf in sorted(_INFL_SUFFIXES, key=len, reverse=True):
            if low_run.endswith(suf) and len(low_run) > len(suf) + 1:
                stem = low_run[: -len(suf)]
                if stem in _EN_WORD_SET:
                    return True
        for suf in _STEM_ONLY_SUFFIXES:
            if low_run.endswith(suf) and len(low_run) > len(suf) + 1:
                stem = low_run[: -len(suf)]
                if stem in _EN_WORD_SET:
                    return True
        return False

    # --- Two-letter function words allowed as "glue tokens" --------------
    # These are overwhelmingly common in any sentence and their appearance
    # mid-run strongly signals a true word boundary.  We require them to be
    # FLANKED by >= 3-char valid words on both sides to avoid nonsense.
    # (Mirror sets are declared above so _is_single_word can reference them.)
    _TWO_LETTER_FUNCTION_WORDS = _TWO_LETTER_FUNCTION_WORDS_REF
    _ONE_LETTER_ALLOWED = _ONE_LETTER_ALLOWED_REF  # only in flanked positions
    _VERY_SHORT_WORDS = frozenset({
        "for", "you", "the", "and", "but", "not",
        "are", "was", "can", "did", "too", "any",
    })  # 3-char words that are valid glue (already captured naturally, documented here)

    def _tail_has_safe_start(low_tail: str) -> bool:
        """True if the tail can be consumed by a valid next word (≥3 chars
        preferred; 2-char glue if the tail-tail is safe)."""
        tlen = len(low_tail)
        if tlen == 0:
            return True
        if tlen < 3:
            # 1-2 char fragment at the very end of the glued run.  Two
            # shapes are SAFE (downstream Pass 2 / Pass 3 of the greedy
            # cursor will consume them as final tokens):
            #   * exactly a recognised 1-letter word ("a"/"I"), OR
            #   * exactly a recognised 2-letter function glue word
            #     ("on", "in", "to", "is", "of", …).  This was previously
            #     rejected as "unsafe standalone", which caused the whole
            #     splitter to fall over on runs like "foxwenton": when
            #     Pass 1 matched "went" (L=4) leaving tail "on", this
            #     gate returned False, so no candidate boundary was ever
            #     pushed for "fox" either, and the entire run fell
            #     through unsplit.
            if tlen == 1:
                return low_tail in _ONE_LETTER_ALLOWED
            # tlen == 2
            return low_tail in _TWO_LETTER_FUNCTION_WORDS
        # Option A: tail itself is a single inflected word
        if _is_single_word(low_tail):
            return True
        # Option B: tail starts with a ≥3-char dictionary word
        max_w = min(_MAX_EN_WORD_LEN, tlen)
        for wlen in range(max_w, 2, -1):
            if low_tail[:wlen] in _EN_WORD_SET:
                # remainder after this ≥3-char word must in turn be safe
                nxt = low_tail[wlen:]
                if len(nxt) == 0:
                    return True
                if len(nxt) == 1 and nxt in _ONE_LETTER_ALLOWED:
                    return True
                if len(nxt) >= 3:
                    return True  # greedily trusts that the next iteration handles it
                # len(nxt) == 2: previously "unsafe standalone" and we skipped
                # this wlen — but that was TOO conservative.  When the exact
                # 2-letter remainder is a recognised glue word (on, in, at,
                # to, of, by, ...), the greedy cursor's Pass 2 (final-
                # position 2-letter glue token) will correctly consume it as
                # the very last token of the run.  E.g. "foxwenton" =
                # fox(3) + went(4) + on(2).  Without this relaxation,
                # _tail_has_safe_start("wenton") returned False at "went"
                # because nxt="on" was rejected as "unsafe standalone 2-letter
                # fragment", so no candidate boundary for "fox" existed, and
                # the entire glued phrase fell through as unsplit.
                if len(nxt) == 2 and nxt in _TWO_LETTER_FUNCTION_WORDS:
                    return True
                # len(nxt) == 2 of non-glue letters → still unsafe; keep
                # searching for a (shorter) initial word whose own tail
                # remainder can be wholly consumed.
        # Option C: tail starts with a 2-letter function word AND what's
        # left after that is ≥3 chars AND itself has a safe start.
        if tlen >= 5 and low_tail[:2] in _TWO_LETTER_FUNCTION_WORDS:
            rest = low_tail[2:]
            if len(rest) >= 3 and _tail_has_safe_start(rest):
                return True
            # Also accept: 2-letter glue + exactly 2 valid letters that
            # can continue (occurs when tail is function-word at penult
            # position of a glued run and remainder is tiny 2-letter
            # glue itself — too short by default but valid in context).
            if len(rest) == 2 and rest in _TWO_LETTER_FUNCTION_WORDS:
                return True
        # Option D: single-letter 'a'/'I' at the very start followed by a
        # safe tail of length ≥3.  Occurs very frequently in "is a fresh
        # page", "and a short paragraph", "or a moment" style glued text.
        if tlen >= 4 and low_tail[:1] in _ONE_LETTER_ALLOWED:
            rest1 = low_tail[1:]
            if len(rest1) >= 3 and _tail_has_safe_start(rest1):
                return True
        # Option E: tail has a short orphan prefix (≤2 chars) followed by a
        # ≥3-char dictionary word.  This handles the common case where a
        # previous greedy match cut off an inflection fragment (e.g. "set"
        # left orphan "s" before "beyond" in "sbeyondthehills").  Without
        # this, tails starting with an unfound 1-2 char fragment were
        # rejected as "no valid start" even though the bulk of the tail is
        # perfectly splittable.  Skip must be ≤ 2 and the post-skip rest
        # must start with a genuine ≥3-char dictionary word (not another
        # orphan).  We also exclude the case where the skipped prefix IS
        # itself a known 1/2-char function word — that's already handled
        # by Option D / Option C respectively.
        if tlen >= 4:
            for _skip in range(1, 3):  # skip 1 or 2 orphan chars only
                if _skip >= tlen - 2:  # leave at least 3 chars after skip
                    break
                _rest_e = low_tail[_skip:]
                _max_w_e = min(_MAX_EN_WORD_LEN, len(_rest_e))
                for _wlen in range(_max_w_e, 2, -1):
                    if _rest_e[:_wlen] in _EN_WORD_SET:
                        # Exclude: skipped prefix is a known 1-letter word
                        # (handled by Option D) or 2-letter glue (Option C).
                        if _skip == 1 and low_tail[:1] in _ONE_LETTER_ALLOWED:
                            continue
                        if _skip == 2 and low_tail[:2] in _TWO_LETTER_FUNCTION_WORDS:
                            continue
                        return True
        return False

    # Compact inflection endings used by the "absorb short tail" guard below.
    # Listed longest-first for determinism.  These are the endings that turn a
    # dictionary base word into "base + ending" legitimate single inflected
    # words — e.g.  "surprise" + "s" = "surprises", "kind" + "ness" = "kindness".
    #
    # NOTE ON "AMBIGUOUS" TOKENS ------------------------------------------------
    # Several English suffixes are also common FREESTANDING dictionary words:
    #     "like" (verb), "less" (adj), "ful" (not standalone but = "full" root),
    #     "able" (adj), "wise" (adj), "wide" (adj), "ible" (no — still suffix-
    #     only), "ion" / "ian" etc.  If the absorb engine sees e.g.
    #         base = "you", ending = "like" → case(b) returns True → absorb!
    #     the glued phrase "youlike..." is NEVER cut and "you" + "like" merge
    #     into a single non-word.  Similarly for
    #         "technology" + "like" → NOT a real word.
    # To protect against this class of false-absorb we split the endings into
    # two buckets:
    #   * STRICT: pure morphological endings that NEVER stand alone as common
    #             words.  e.g. "ness", "ment", "tion", "ing", "s", "ed", "ly"...
    #   * AMBIG: endings that COINCIDE with common dictionary words of length
    #             ≥ 3 (or are ≥3 letters and legitimately used both ways).
    # AMBIG endings are allowed through case (b)/(c) ONLY when combined
    # (base+ending) is itself in the dictionary — i.e. the case (a) rule.  In
    # other words, "childlike" passes because "childlike" is a dict word, but
    # "youlike" / "youl" + "ike" / "technologylike" fail because they are not
    # real dictionary compounds.
    _INFLECT_STRICT = (
        "ability", "ibility", "fulness", "lessness", "mental", "lessly",
        "ology", "graphy", "craft", "ness", "ment", "ship", "hood", "ward",
        "wards",
        "ious", "eous", "ical",
        # 4+ character endings are IMMUNE to the mid-sentence absorb bug that
        # plagued 3-letter "-ise".  Restore them to STRICT for broad coverage:
        "tion", "sion", "ction", "ssion", "xion",
        "ance", "ence", "ancy", "ency",
        "ual", "ial", "ative", "itive", "ally", "ially", "ier", "iest",
        "ing", "edly", "antly", "ently",
        "ies", "ingly", "ed", "es", "ly", "er", "ior",
        "al", "en", "ten", "den",
        "ture", "sure",
        "ism",
        "ted", "ded",
        "s", "d",
    )
    # Ambiguous endings (token is a real dictionary word ≥ 3 chars in its own
    # right).  Only accept as suffix via case (a) combined-hit.
    _INFLECT_AMBIGUOUS_AS_SUFFIX = frozenset({
        "like", "less", "ful", "able", "ible",
        "wise", "wide",
        "ous", "ive", "ary", "ery", "ory", "ist",
        "dom",  # used; but 'dom' as standalone (French) less common than as
                # suffix.  Keep as ambiguous to force case(a) check for
                # safety: "freedom" / "kingdom" / "boredom" all in dict anyway.
        "ice",  # standalone noun vs. suffix: "justice".  As ambiguous.
        "ity",  # standalone (slang) 'ity' rare.  Keep ambiguous to double-check
                # against dict as combined since suffixes like -ability already
                # cover most cases.
        "ate", "ite",  # standalone homonyms: 'ate' (eat past), 'ite' (mineral).
        "ure",  # unit of resistivity (ambiguous standalone homonym).
        "ion",  # 3-letter ending.  "tion" is 4+ chars and restored to STRICT
                # (it is unambiguous and the suffix engine needs it for broad
                # coverage), but plain "ion" can still collide mid-sentence so
                # stays AMBIG with case-(a) dict-only gate.
        # ---- Moved from STRICT (see note above) ----
        # 3-letter verbal / nominal endings that can masquerade as glued
        # mid-sentence tokens when paired with the case-(c) chaining engine
        # (which used to build "isessen" out of "ise"+"s"+"en", absorbing the
        # start of "is essential" after "what" and killing the whole split).
        "ize", "ise", "ify",
    })
    _INFLECT_SHORT = tuple(dict.fromkeys((*_INFLECT_STRICT, *_INFLECT_AMBIGUOUS_AS_SUFFIX)))

    def _is_inflected(base: str, tail_partial: str) -> bool:
        """True if appending `tail_partial` to dictionary word `base` yields a
        plausible inflected/composed single English word.

        Four cases, priority order (see note above on AMBIG endings):
          (a) the whole combined string is itself in the dictionary, OR
          (b) `tail_partial` equals a STRICT morphological ending (longest-
              match accepted when len(base) >= 3), OR
          (c) `tail_partial` is a GREEDY tokenisation of ≥1 STRICT-only
              morphological ending tokens (chain, e.g. "s" + "ly" = "sly"), OR
          (d) [SAFE AMBIG] `tail_partial` (or its greedy tokenisation over
              STRICT ∪ AMBIG) uses STRICT-only tokens except possibly at the
              very END, when the final token is AMBIG AND we can verify it
              produces a dictionary word when stripped from tail_partial and
              combined with base as base+ambig_tok on the DICT.  (This is
              effectively case(a) for the suffix tail when chained with strict
              tokens before).
        """
        combined = base + tail_partial
        if combined in _EN_WORD_SET:
            return True  # case (a)

        STRICT = set(_INFLECT_STRICT)
        AMBIG = set(_INFLECT_AMBIGUOUS_AS_SUFFIX)

        # --- Case (b): tail_partial is exactly one STRICT token --------------
        if tail_partial in STRICT:
            return len(base) >= 3
        # (If tail_partial is one AMBIG token WITHOUT a case(a) combined-hit,
        # we MUST refuse because e.g. base="you" + tail="like" would match as
        # case(b) if AMBIG tokens were allowed here, and that's exactly the
        # bug we're fixing: "youlike..." would absorb the "like" boundary.)

        # --- Case (c): tail_partial tokenises 100% STRICT (morph chain) ----
        # NOTE: case (c) only applies to GENUINE multi-ending combinations
        # (e.g. "tion"+"s" = "tions", "ness"+"es" = "nesses").  We deliberately
        # require ≥ 2 tokens AND combined length ≥ 4 because any shorter
        # sequence can be accidentally tokenised out of letter overlap with
        # STRICT endings:
        #     BAD: "end" = "en" (STRICT) + "d" (STRICT) — absorbs the start
        #          of the real next word "enduring" and makes the glued
        #          following tail "uringthem" unsplittable.
        #     BAD: "edly" is already a single STRICT token (no chain needed).
        # Any single STRICT ending is handled already by case (b).  Chaining
        # is only valid for proven multi-morpheme clusters of length ≥ 4
        # using at least 2 distinct tokens.
        INFLECT_CHAIN_TOKENS = {*STRICT, *AMBIG}
        rest = tail_partial
        chain_tokens = 0
        c_ok = True
        while rest:
            found_tok = False
            for tok in sorted(INFLECT_CHAIN_TOKENS, key=len, reverse=True):
                if rest.startswith(tok) and len(tok) > 0:
                    if tok in AMBIG:
                        continue
                    rest = rest[len(tok):]
                    chain_tokens += 1
                    found_tok = True
                    break
            if not found_tok:
                c_ok = False
                break
        # case (c) REMOVED — too loose.  'deser' (=d+es+er, all STRICT) matches
        # any base like 'the' -> fake absorb 'thedeser'.  Real multi-morpheme
        # compounds are caught by case (a) (combined in dict) already.
        pass

        return False

    # --- Core splitter ----------------------------------------------------
    def _split_latin_run(run: str) -> str:
        """Split a single Latin-letter run into space-separated words.

        Conservative greedy strategy.  Returns the run unsplit if no
        high-confidence segmentation exists.
        """
        n = len(run)
        if n < 4:
            return run
        low = run.lower()

        # Short-circuit: entire run is a valid (possibly inflected) word
        _isw = _is_single_word(low)
        if _isw:
            print(chr(91)+chr(68)+chr(66)+chr(71)+chr(93)+chr(32)+chr(95)+chr(105)+chr(115)+chr(95)+chr(115)+chr(105)+chr(110)+chr(103)+chr(108)+chr(101)+chr(32)+chr(114)+chr(101)+chr(116)+chr(117)+chr(114)+chr(110)+chr(101)+chr(100)+chr(32)+chr(84)+chr(114)+chr(117)+chr(101)+chr(32)+chr(102)+chr(111)+chr(114)+chr(32)+repr(run))
            return run

        words: list[str] = []
        i = 0
        failed = False
        while i < n:
            matched_len = 0
            remainder = n - i
            max_try = min(_MAX_EN_WORD_LEN, remainder)

            # Pass 1: ≥3-char dictionary words, longest first.
            # SAFETY CHECK before accepting a match at position i of length L:
            #   * tail must be splittable (via _tail_has_safe_start).
            #   * BUT if the candidate's last-char + next-few chars together
            #     form a standard inflection ending ON the candidate base
            #     (i.e. candidate L is too short because it cuts off the end
            #     of a plural/conjugation), REJECT this L and try shorter.
            # Example: "surprisesand..." → at some cursor we could match
            # "surprise" (L=8) leaving "sand..." which looks safe, but the
            # first letter of tail is "s" → "surprise" + "s" = "surprises" is a
            # valid inflected word → we MUST NOT cut here; keep scanning with
            # shorter L or later passes will eventually treat "surprises" as
            # the boundary by re-matching via _is_single_word.
            #
            # LOOKAHEAD TIEBREAKER (added 2026-08-29):
            #   Pure "longest first word" greedy fails when two boundaries
            #   are both individually valid but produce dramatically
            #   different FOLLOWING-word qualities.  Two recurring examples:
            #       "staystill"   → L=5 "stays"  then  "till"    (4)  ← BAD
            #                       → L=4 "stay"   then  "still"   (5)  ← GOOD
            #       "growthrough" → L=6 "growth" then  "rough"   (5)  ← BAD
            #                       → L=4 "grow"   then  "through" (7)  ← GOOD
            #   Fix: instead of breaking on the FIRST valid L (longest
            #   first-word), collect ALL valid-candidate boundaries across
            #   the entire L scan, score each by the LENGTH of the next
            #   ≥3-char dictionary word that would start at the resulting
            #   tail (next-word-length, NWL), pick the MAX NWL, and tie-
            #   break on first-word length (longer first word still wins
            #   when the following-word prediction is equal).  This single
            #   change correctly repairs both pathological splits above
            #   without degrading the common cases.
            def _can_fully_split(s: str) -> bool:
                """Quick DP: can s be fully partitioned into dict words?"""
                if len(s) == 0:
                    return True
                dp = [False] * (len(s) + 1)
                dp[0] = True
                for i in range(1, len(s) + 1):
                    m = min(_MAX_EN_WORD_LEN, i)
                    for L in range(m, 0, -1):
                        if not dp[i - L]:
                            continue
                        word = s[i - L:i]
                        if L >= 3 and word in _EN_WORD_SET:
                            dp[i] = True; break
                        elif L == 2 and word in _TWO_LETTER_FUNCTION_WORDS:
                            dp[i] = True; break
                        elif L == 1 and word in _ONE_LETTER_ALLOWED:
                            dp[i] = True; break
                return dp[len(s)]

            def _nwl(tail_str: str, _no_func_bonus: bool = False) -> int:
                """Next-word length for lookahead tiebreaking.

                Scoring hierarchy (higher = better boundary):

                1. Empty tail → 9999 (boundary finishes the run).
                2. FULLY SPLITTABLE tail → 9000 + bonus + first_word_len:
                   - +500 bonus if tail starts with a SHORT (≤3-char)
                     function word ("of", "the", "to") — this is a strong
                     signal that the current cut is at a natural word
                     boundary rather than mid-way through a content word.
                   - Plus the raw first-word length of the tail, so longer
                     tail-openers still beat shorter ones among equals.
                   Example: "part"→"softhe" scores 9004 (tail starts with
                     4-char "soft", no bonus). "parts"→"ofthe" scores 9502
                     (tail starts with 2-char "of", gets +500 bonus, then
                     +2 for first-word length). The function-word bonus
                     correctly prefers "parts of the" over "part soft he".
                   Example: "stay"→"still" scores 9005 (5-char opener).
                     "stays"→"till" scores 9004 (4-char opener). No bonus
                     either way, so the longer tail-opener wins — "stay
                     still" over "stays till".
                3. Non-splittable tail → raw first-word length only (so
                   "grow"→"through"(7) beats "growth"→"rough"(4)).

                When _no_func_bonus=True (used by Pass 2/3 short candidates),
                the +500 function-word bonus is suppressed.  This prevents
                "so"+"me"+"thing" (Pass 2 "so" with tail "me..." getting
                +500 for "me") from beating the correct Pass 1 match
                "something".  Pass 2/3 already win naturally when Pass 1
                has no good ≥3-char candidates; they should not also win
                via bonus inflation when Pass 1 found a perfect fit.
                """
                if len(tail_str) == 0:
                    return 9999
                m2 = min(_MAX_EN_WORD_LEN, len(tail_str))
                # Determine first-word length of tail (for both scoring paths)
                # ORPHAN-AWARE: skip up to 2 orphan leading chars to find
                # the first real dict word.  Record orphan_count so we can
                # penalize tails with orphan prefixes (they indicate the cut
                # was slightly off, e.g. "hear"→tail="snothing" vs "he"→tail="arsnothing").
                first_len = 0
                orphan_count = 0
                for _skip in range(3):  # try 0, 1, 2 orphan skips
                    if _skip > len(tail_str) - 3:
                        break
                    _t = tail_str[_skip:]
                    _found = False
                    for L2 in range(min(_MAX_EN_WORD_LEN, len(_t)), 2, -1):
                        if _t[:L2] in _EN_WORD_SET:
                            first_len = L2; _found = True; break
                    if not _found and len(_t) >= 2 and _t[:2] in _TWO_LETTER_FUNCTION_WORDS:
                        first_len = 2; _found = True
                    if not _found and len(_t) >= 1 and _t[:1] in _ONE_LETTER_ALLOWED:
                        first_len = 1; _found = True
                    if _found:
                        orphan_count = _skip
                        break
                # FULLY SPLITTABLE tail — quality-aware scoring.
                # (fully-splittable means tail can be partitioned from pos 0;
                # it normally implies orphan_count == 0 anyway)
                if _can_fully_split(tail_str):
                    score = 9000
                    # Function-word bonus for tails starting with short
                    # function words (1-3 letters).  This is a strong signal
                    # that the current cut is at a natural word boundary
                    # rather than mid-way through a content word.
                    # E.g. "parts of the" beats "part soft he" because tail
                    # "ofthe" starts with 2-letter func "of", triggering bonus.
                    #
                    # NOTE: 3-letter func bonus can occasionally cause
                    # suboptimal splits like "ones its" instead of "one sits",
                    # but this is outweighed by the cases it fixes (e.g.
                    # "throbs and" beating "throb sand").  The shortcandidate
                    # _no_func_bonus=True path already prevents this bonus
                    # from corrupting Pass 2/3 competition.
                    if not _no_func_bonus and first_len >= 1 and first_len <= 3 and tail_str[:first_len] in _FUNCTION_WORDS_FOR_BONUS:
                        score += 500
                    score += first_len
                    return score
                # Non-splittable: first_len minus orphan penalty.
                # "snothing" → first_len=7, orphan=1 → NWL=6
                # "arsnothing" → first_len=1 (a), orphan=0 → NWL=1
                # So Pass-1 cuts with tiny-orphan tails beat Pass-2/3 cuts with no orphans but worse content.
                if first_len == 0:
                    return 0
                return first_len - orphan_count
            # Candidate tuple: (boundary_len, next_word_len, first_word_len)
            # Sort key priority: NWL DESC, then first-word len DESC, then
            # boundary DESC — deterministically favours the choice whose
            # FOLLOWING token is strongest, and falls back to "longer first
            # word" when both predict the same next token.
            candidates: list[tuple[int, int, int]] = []
            for L in range(max_try, 2, -1):
                candidate = low[i:i + L]
                if candidate not in _EN_WORD_SET:
                    continue
                tail = low[i + L:]
                # Anti-overcut guard: candidate + K chars of tail = a valid
                # inflected word AND the remaining tail is still splittable.
                absorbed_by_inflection = False
                K_found = 0
                if tail:
                    absorb_limit = min(len(tail), 8)
                    for K in range(absorb_limit, 0, -1):
                        ending = tail[:K]
                        if _is_inflected(candidate, ending):
                            if K <= 2:
                                whole = candidate + ending
                                rest = tail[K:]
                                if not (rest == "" or whole in _EN_WORD_SET):
                                    continue
                            absorbed_by_inflection = True
                            K_found = K
                            break
                if absorbed_by_inflection:
                    new_L = L + K_found
                    absorb_pushed = False
                    if new_L <= remainder:
                        new_tail = low[i + new_L:]
                        # K<=2 absorb + s/d-start double guard
                        absorb_ok = True
                        if K_found <= 2 and len(new_tail) >= 2 and new_tail[:1] in ("s", "d"):
                            if len(new_tail[1:]) < 3:
                                absorb_ok = False
                        if absorb_ok and (len(new_tail) == 0 or
                                          _tail_has_safe_start(new_tail)):
                            # Case (a) bonus: when the combined word is a
                            # known dictionary word formed by a LONG
                            # ending (K >= 3, e.g. "everyday" =
                            # "every"+"day"), give absorb a strong NWL
                            # bonus so it wins over the standard split.
                            # For K <= 2 (e.g. "stays" = "stay"+"s"),
                            # DON'T bonus — the inflection is ambiguous
                            # and the standard split may be correct
                            # (e.g. "staystill" → "stay still" not
                            # "stays till").
                            _combined = candidate + tail[:K_found]
                            _absorb_nwl = _nwl(new_tail)
                            if K_found >= 3 and _combined in _EN_WORD_SET:
                                _absorb_nwl = max(_absorb_nwl, 10)
                            candidates.append((new_L, _absorb_nwl, new_L))
                            absorb_pushed = True
                    # When the absorb match is via case (a) — combined
                    # word in dictionary (e.g. "everyday" = "every"+"day")
                    # — the standard path may also be valid (e.g. "nowhere"
                    # = "now"+"here").  Let BOTH compete but give absorb a
                    # bonus: the combined word being a known dictionary
                    # word is a strong signal it should stay together.
                    # When the match is via case (b)/(c) — pure suffix —
                    # skip standard path (no competing boundary).
                    _absorb_via_case_a = (candidate + tail[:K_found]) in _EN_WORD_SET
                    if K_found >= 3 and absorb_pushed and not _absorb_via_case_a:
                        continue
                # Standard (non-absorbed) tail-safety path.  Runs both for
                # the no-absorb case AND for K<=2 ambiguous-absorb cases
                # (after an absorb candidate was already pushed).
                if len(tail) == 0:
                    # Only record if absorb didn't already capture end-of-run
                    if not absorbed_by_inflection:
                        candidates.append((L, _nwl(tail), L))
                    continue
                if _tail_has_safe_start(tail):
                    # s/d stand-alone fragment double-check: prevent
                    # splitting "you" from "day" when tail = "day" (where
                    # "d" is the start of a real word, not an inflection).
                    # BUT: if the tail itself IS a known word (≥3 chars in
                    # the dictionary), accept it regardless — e.g. "don"
                    # is a real word so "youdon" → "you don" is correct.
                    if len(tail) >= 2 and tail[:1] in ("s", "d"):
                        if not _tail_has_safe_start(tail):
                            continue
                        following = tail[1:]
                        if len(following) < 3:
                            # Relax: if the FULL tail is a known word,
                            # accept the boundary anyway.  This fixes
                            # "youdon" → "you don" (don is a real word).
                            if tail not in _EN_WORD_SET and not _is_single_word(tail):
                                continue
                    candidates.append((L, _nwl(tail), L))
                    continue
            if candidates:
                candidates.sort(key=lambda t: (t[1], t[2], t[0]), reverse=True)
                matched_len = candidates[0][0]

            # Pass 2: 2-letter function word — collect as candidate too so
            # it competes with Pass-1 dictionary words on equal footing.
            # This is critical for cases like "agentle" where Pass-1 picks
            # "agent" (5 chars) leaving orphan "le", but Pass-2/3 could pick
            # "a" (1) + "gentle" (6) for a far cleaner split.  Previously
            # Pass 2/3 only ran when Pass-1 found nothing, so bad Pass-1
            # choices were never challenged.
            #
            # IMPORTANT: Pass 2/3 short candidates (L≤2) get FULL NWL —
            # previously they were discounted by 0.2 which caused "toy"
            # (L=3) to beat "to" (L=2) in "suitedtoyour" because
            # "toy"+"our" NWL=9003 > "to"+"your" NWL=9005×0.2=1801.
            # Full NWL lets the tail quality decide: if the tail after a
            # short function word is fully splittable (e.g. "your"), the
            # cut is at a natural boundary and should win over an
            # accidental ≥3-char match like "toy".  For Pass-1 ≥3-char
            # candidates, we rely on the _can_fully_split NWL scoring to
            # ensure the longer word only wins when it's truly better.
            if remainder >= 2:
                if remainder == 2:
                    cand2 = low[i:i + 2]
                    if cand2 in _TWO_LETTER_FUNCTION_WORDS:
                        candidates.append((2, _nwl("", _no_func_bonus=True), 2))
                elif remainder >= 5:
                    cand2 = low[i:i + 2]
                    if cand2 in _TWO_LETTER_FUNCTION_WORDS:
                        rest2 = low[i + 2:]
                        # Pass 2 short function words DON'T need
                        # _tail_has_safe_start — a 2-char function word
                        # is itself a strong natural-boundary signal.
                        # E.g. "softhe" → "so"+"the" beats Pass-1's
                        # "soft"+"he", but "fthe" would fail the safe-
                        # start check ("ft" isn't a dict word).
                        #
                        # SCORING: _nwl(rest2) often returns 0 here
                        # because the tail starts with orphan chars
                        # (e.g. "fthe" starts with "ft").  But if the
                        # tail CAN be split into ≥2 dictionary words
                        # (e.g. "fthe" → ["f","the"] — "the" is dict),
                        # that's a strong signal the Pass-2 cut is
                        # better than Pass-1's greedy match.  Use a
                        # boosted score when the tail has rich dict
                        # content.
                        #
                        # IMPORTANT: _no_func_bonus=True prevents Pass 2
                        # short candidates from getting the +500 function-
                        # word bonus when their tail starts with a func
                        # word.  Without this, "so"+"me"+"thing" would beat
                        # "something" because tail "me..." triggers +500.
                        _p2_nwl = _nwl(rest2, _no_func_bonus=True)
                        if _p2_nwl < 9000:
                            # Count dict words in tail — but NO orphan
                            # skipping.  Require the tail to START with
                            # valid dict words; orphan-prefixed tails
                            # (e.g. "lwaysloved..." → orphan 'l' then
                            # "way") should NOT get the rich-content boost,
                            # because that orphan is itself a signal that
                            # the Pass-2 cut was wrong.
                            _tail_word_count = 0
                            _t = rest2
                            while len(_t) >= 3:
                                _found = False
                                for _L in range(min(10, len(_t)), 2, -1):
                                    if _t[:_L] in _EN_WORD_SET:
                                        _tail_word_count += 1
                                        _t = _t[_L:]
                                        _found = True
                                        break
                                if not _found:
                                    break  # orphan at head — stop, no boost
                            if _tail_word_count >= 3:
                                _p2_nwl = 9000 + 500  # match fully-splittable + func bonus
                        candidates.append((2, _p2_nwl, 2))

            # Pass 3: single-letter 'a' / 'I' — same treatment as Pass 2.
            if remainder >= 1:
                if remainder == 1:
                    cand1 = low[i:i + 1]
                    if cand1 in _ONE_LETTER_ALLOWED and words:
                        candidates.append((1, _nwl("", _no_func_bonus=True), 1))
                elif remainder >= 4:
                    cand1 = low[i:i + 1]
                    if cand1 in _ONE_LETTER_ALLOWED:
                        rest1 = low[i + 1:]
                        # Same as Pass 2 — skip safe-start check; boost
                        # NWL when tail has ≥2 dict words.
                        #
                        # IMPORTANT: _no_func_bonus=True — see Pass 2 note.
                        _p3_nwl = _nwl(rest1, _no_func_bonus=True)
                        if _p3_nwl < 9000:
                            # Same as Pass 2 — NO orphan skipping.
                            # Require tail to START with valid dict words.
                            _tail_word_count = 0
                            _t = rest1
                            while len(_t) >= 3:
                                _found = False
                                for _L in range(min(10, len(_t)), 2, -1):
                                    if _t[:_L] in _EN_WORD_SET:
                                        _tail_word_count += 1
                                        _t = _t[_L:]
                                        _found = True
                                        break
                                if not _found:
                                    break  # orphan at head — no boost
                            if _tail_word_count >= 3:
                                _p3_nwl = 9000 + 500
                        candidates.append((1, _p3_nwl, 1))

            # Re-sort with Pass 2/3 candidates included; pick the best.
            if candidates:
                candidates.sort(key=lambda t: (t[1], t[2], t[0]), reverse=True)
                matched_len = candidates[0][0]
            if matched_len == 0:
                # Fallback: try to consume 1-2 orphan characters and keep
                # going.  When the greedy dict scan finds NO word at this
                # position, it often means a short inflection fragment was
                # left orphaned by a previous cut (e.g. "set" → orphan "s"
                # before "beyond" in "sbeyondthehills").  Emitting the
                # orphan as its own token lets the rest of the run be split.
                # We verify that after skipping the orphan, the remaining
                # tail can still produce ≥2 dictionary words — otherwise
                # we give up.
                _orphan_ok = False
                for _ot in (1, 2):
                    if i + _ot > n:
                        break
                    _rest_v = low[i + _ot:]
                    if len(_rest_v) >= 3:
                        _vscan = 0
                        _vtokens = 0
                        _vn = len(_rest_v)
                        while _vscan < _vn and _vtokens < 2:
                            _vf = False
                            for _vL in range(min(_MAX_EN_WORD_LEN, _vn - _vscan), 2, -1):
                                if _rest_v[_vscan:_vscan + _vL] in _EN_WORD_SET:
                                    _vtokens += 1
                                    _vscan += _vL
                                    _vf = True
                                    break
                            if not _vf:
                                break
                        # Accept if orphan-less tail has >= 1 dict word
                        # at its start (previously required >= 2, which
                        # rejected valid cases like "snothing" where only
                        # "nothing" is a single dict word).
                        if _vtokens >= 1:
                            matched_len = _ot
                            _orphan_ok = True
                            break
                    elif len(_rest_v) <= 2:
                        # Tiny rest — orphan + rest together may be ok
                        matched_len = _ot
                        _orphan_ok = True
                        break
                # Safety: don't emit 1-char orphan if previous token was
                # also 1-char (prevents adjacent "s t a y i n g" style garbage).
                if _orphan_ok and matched_len == 1 and words and len(words[-1]) == 1:
                    _orphan_ok = False
                    matched_len = 0
                if not _orphan_ok:
                    failed = True
                    break
            words.append(run[i:i + matched_len])
            i += matched_len

        if failed or not words:
            return run

        # Quality gate
        result = " ".join(words)
        if result.replace(" ", "") != run:
            return run
        total_letters = sum(len(w) for w in words)
        # Average letter length threshold lowered to 2.0.  This allows
        # legitimate splits like "i can do for you" (avg 2.4) or "to be
        # or not to be" (avg 2.3) which have a high proportion of short
        # function words.  Previously at 2.5 these were rejected.
        if len(words) >= 5 and total_letters / len(words) < 2.0:
            return run
        # No adjacent 1-char tokens
        for w_a, w_b in zip(words, words[1:]):
            if len(w_a) == 1 and len(w_b) == 1:
                return run
        # Short-token ratio: raised from 40% → 55% so everyday high-
        # frequency prose like "in life and in technology like" (contains
        # in/and/in three 2-char words) has a chance.
        shorties = sum(1 for w in words if len(w) <= 2)
        if shorties and len(words) > 2 and shorties / len(words) > 0.55:
            return run
        return result

    def _split_plain(plain: str) -> str:
        if not plain:
            return plain

        # --- Capital-letter boundary pre-split -----------------------------
        # LLM glued output often preserves sentence-initial capitals and
        # proper-noun capitals.  Those uppercase letters are extremely
        # strong word-boundary signals that the pure dictionary matcher
        # otherwise misses.  We split here BEFORE running the Latin-run
        # splitter, on these deterministic rules:
        #   1. `AbcDef...`    → split between "c" and "D"   (lower|upper)
        #   2. `ABCDef...`    → split before "Def"         (multi-cap tail)
        #   3. `abc123Def...` → split before "Def"         (digit|upper)
        #   4. `ABC`          → keep as-is (acronym)
        # The inserted zero-width marker (\u200b) lets the downstream
        # alpha-only run splitter see two independent runs while keeping
        # the non-alpha segments pipeline intact.
        tmp_chars: list[str] = []
        plen = len(plain)
        for i, ch in enumerate(plain):
            tmp_chars.append(ch)
            if i == plen - 1:
                break
            nxt = plain[i + 1]
            if not nxt.isalpha():
                continue
            if ch.isalpha() and nxt.isupper():
                # Rule 1 & 3: lowercase->uppercase, OR any-letter inside a
                # run where the previous char was NOT a capital AND the next
                # char is capital followed by at least one lowercase
                # (heuristic: sentence start / proper noun trigger, not an
                # acronym continuation).
                if ch.islower() or ch.isdigit():
                    tmp_chars.append("\u200b")
                elif ch.isupper() and i + 2 < plen and plain[i + 2].islower():
                    # Rule 2: "ABCDef" → keep "ABC" together, insert
                    # boundary before "Def" (hence insert AFTER current
                    # char which is 'C' — 'A'→'B'→'C' but we're at the last
                    # capital of the run).  More accurately: we're at
                    # position i where ch is the Nth uppercase in a row,
                    # and nxt+nxt+1 is capital-followed-by-lower (proper
                    # noun shape).  Then the boundary is BETWEEN i and i+1
                    # only if plain[i+2] is lowercase.
                    tmp_chars.append("\u200b")
        plain = "".join(tmp_chars)

        parts: list[str] = []
        i = 0
        n = len(plain)
        # Punctuation characters after which a following letter or
        # (opening) quote must be separated by a space.  Covers the
        # common glued output from LLMs where sentence punctuation
        # fuses with the next word, e.g.:
        #   "choose.Even small"   → should be "choose. Even small"
        #   "comfortable,but"     → should be "comfortable, but"
        #   "general?Letmeknow"   → should be "general? Let me know"
        #   "Sure!Here"           → should be "Sure! Here"
        #   'meaningful."Anda'    → should be 'meaningful. "And a'
        #   "you:\"Everyday..."  → should be 'you: "Everyday...'
        # We use the same zero-width marker \u200b here so downstream
        # processing remains uniform (the final .replace turns all
        # markers into real spaces).
        # NOTE: '.' is INTENTIONALLY excluded from this set.  Dot-after-letter
        # is ambiguous: ".baidu.com" (URL), "Mr.Smith" (honorific), "U.S.A."
        # (acronym), "file.pdf" (extension) must NOT gain a space.  We only
        # add a space after '.' when the following char is UPPERCASE (i.e.
        # sentence-final punctuation like "end.Next" where "Next" starts a
        # new sentence — covered by the dedicated special-case below).
        _SPACE_AFTER_PUNCT = frozenset("?!,;:")
        # Standard English contraction suffixes (longest first for determinism).
        # An apostrophe followed by one of these, with at least one letter
        # before the apostrophe, forms a recognised contraction such as
        # "I'll", "don't", "we're", "I've", "I'm", "he'd", "it's".  We treat
        # the whole contraction as a single non-splittable token and insert a
        # word boundary right after it so the following glued letters can be
        # independently segmented by the dictionary splitter.
        _CONTRACTION_SUFFIXES = ("ll", "re", "ve", "s", "t", "m", "d")
        while i < n:
            ch = plain[i]
            if ch.isalpha() or ch == "'":
                j = i
                while j < n and (plain[j].isalpha() or plain[j] == "'"):
                    j += 1
                raw = plain[i:j]
                # --- Contraction-aware sub-split ---------------------------
                # If the run contains apostrophes, try to cut at recognised
                # contraction boundaries so that (e.g.) "I'lladjustforyou"
                # becomes ["I'll"] + ["adjustforyou"] and only the pure
                # letter fragment goes through the dictionary matcher.
                if "'" in raw:
                    sub_i = 0
                    raw_n = len(raw)
                    # We'll walk the run.  Every time we see an apostrophe
                    # preceded by letters and followed by a recognised
                    # contraction suffix, we consume [prefix + ' + suffix]
                    # as one token, then emit the remaining letters through
                    # _split_latin_run.
                    while sub_i < raw_n:
                        # Find next apostrophe position starting from sub_i
                        apos_pos = raw.find("'", sub_i)
                        if apos_pos == -1:
                            # No more apostrophes — remaining is pure letters
                            rest = raw[sub_i:]
                            if rest:
                                parts.append(_split_latin_run(rest))
                            break
                        # Letters before the apostrophe must exist
                        if apos_pos == sub_i:
                            # Apostrophe at start of segment (e.g. quoted
                            # fragment like "'Wouldyou").  Just advance past
                            # it as a literal character.
                            parts.append("'")
                            sub_i = apos_pos + 1
                            continue
                        before = raw[sub_i:apos_pos]
                        # Match longest contraction suffix after the apostrophe
                        matched_sfx = None
                        after_apos = raw[apos_pos + 1:]
                        for sfx in _CONTRACTION_SUFFIXES:
                            if after_apos.startswith(sfx):
                                matched_sfx = sfx
                                break
                        if matched_sfx is None:
                            # Unknown suffix: process [before] as letters,
                            # emit apostrophe literally, continue with the
                            # rest of the suffix part (will fall into the
                            # same branch later or get letters processed).
                            if before:
                                parts.append(_split_latin_run(before))
                            parts.append("'")
                            sub_i = apos_pos + 1
                            continue
                        # Valid contraction.
                        sfx_end = apos_pos + 1 + len(matched_sfx)
                        contraction_tok = before + "'" + matched_sfx
                        # Emit the letters-before-apostrophe (if any pure
                        # letter prefix needs splitting — e.g. in
                        # "youdon'tknow" the "youdo" part before the 't is
                        # "youdo" which itself contains a glued boundary).
                        # Actually: for a standard contraction the "before"
                        # part is the base pronoun/aux like "I", "you",
                        # "don", "we", etc.  Some of these ARE glued (e.g.
                        # "youdon't" → before = "youdon" = "you" + "don").
                        # We can't pass the contraction through
                        # _split_latin_run because of the apostrophe.  So
                        # instead: run the "before" part through the splitter
                        # ONLY if "before" looks like it has glued words
                        # (length >= 5).  Small bases (<=4 letters like "I",
                        # "you", "don", "we", "they", "won", "can") we keep
                        # merged since they're legitimate contraction bases
                        # in common usage.
                        if len(before) >= 5:
                            parts.append(_split_latin_run(before))
                        else:
                            parts.append(before)
                        parts.append("'" + matched_sfx)
                        # Insert boundary marker
                        parts.append("\u200b")
                        sub_i = sfx_end
                        continue
                else:
                    parts.append(_split_latin_run(raw))
                i = j
            else:
                parts.append(ch)
                # Insert zero-width separator when punctuation is followed
                # directly by a letter (sentence continuation) or by a
                # quote character that starts a quoted phrase.
                if i + 1 < n:
                    nxt = plain[i + 1]
                    if ch == '.':
                        # Dot special case: add space only when the next char
                        # is UPPERCASE AND the preceding char is NOT uppercase.
                        # Rationale:
                        #   "end.Next"    → '.' after lowercase 'd', before
                        #                   uppercase 'N' → sentence boundary,
                        #                   add space ✓
                        #   "U.S.A."      → '.' between two uppercases →
                        #                   acronym, keep together ✓
                        #   "Mr.Smith"    → '.' after lowercase 'r', before
                        #                   uppercase 'S' → honorific + name,
                        #                   add space ✓
                        #   ".baidu.com"  → '.' before lowercase → URL, no
                        #                   space ✓
                        #   "file.pdf"    → '.' before lowercase → extension,
                        #                   no space ✓
                        if (
                            nxt.isupper()
                            and (i == 0 or not plain[i - 1].isupper())
                        ):
                            parts.append("\u200b")
                    elif ch in _SPACE_AFTER_PUNCT:
                        if nxt.isalpha() or nxt in ('"', "'"):
                            parts.append("\u200b")
                i += 1
        # Remove the zero-width markers that survived (they're visual noise)
        return "".join(parts).replace("\u200b", " ")


    # Process segments
    out: list[str] = []
    for is_protected, chunk in segments:
        if is_protected:
            out.append(chunk)
        else:
            out.append(_split_plain(chunk))
    final_text = "".join(out)

    # Post-process: ensure space after common quote/punct patterns that
    # the Latin splitter never sees because they're not pure letters.
    # Examples: '">What' -> '"> What', '"Hello' -> '" Hello'.
    # NOTE: intentionally omit '.' from the punct class — dot-after-letter
    # commonly appears in URLs (.baidu, .com), abbreviations (U.S.A., Mr.),
    # and file extensions (.pdf); adding a space there would break those.
    # Protected segments (code, URLs, markdown links) are already passed
    # through unchanged.
    # NOTE: char class below is ONLY [">] (quote + angle-bracket).
    # We intentionally EXCLUDE '.' — dot-after-letter commonly appears
    # in URLs (.baidu, .com), abbreviations (U.S.A., Mr.), and file
    # extensions (.pdf); adding a space there would break those.
    # Protected segments (code, URLs, markdown links) already pass through.
    final_text = re.sub(r'([">])([A-Za-z])', r'\1 \2', final_text)
    final_text = re.sub(r'([A-Za-z])(")', r'\1 \2', final_text)

    return final_text

    def _split_latin_run_dp(run: str) -> list[str]:
        """Split a Latin run into words using dynamic programming.

        Args:
            run: A string of consecutive Latin letters/apostrophes

        Returns:
            A list of words (or individual characters if no valid split found)
        """
        n = len(run)
        if n <= 1:
            return [run] if run else []

        # Convert to lowercase for dictionary lookup
        low = run.lower()

        # DP array: dp[i] = best score for suffix starting at position i
        # word[i] = the word chosen at position i
        dp = [0.0] * (n + 1)
        word_choice = [""] * (n + 1)

        # Step 1: Pre-compute which substrings are valid words
        # valid[i][j] = True if run[i:j] is a valid word
        valid = [[False] * (n + 1) for _ in range(n)]

        for i in range(n):
            for j in range(i + 1, min(i + 20, n + 1)):  # max word length = 20
                candidate = low[i:j]
                if candidate in _EN_WORD_SET:
                    valid[i][j] = True

        # Step 2: DP from right to left
        for i in range(n - 1, -1, -1):
            best_score = -float('inf')
            best_word = ""

            for j in range(i + 1, min(i + 20, n + 1)):
                if valid[i][j]:
                    # Score = word score + DP score for remainder
                    word_score = _get_word_score(low[i:j])
                    total_score = word_score + dp[j]

                    if total_score > best_score:
                        best_score = total_score
                        best_word = run[i:j]

            # If no valid word found, use single character
            if not best_word:
                best_word = run[i]
                best_score = -1.0  # Penalty for unknown word

            dp[i] = best_score
            word_choice[i] = best_word

        # Step 3: Reconstruct the split from word_choice
        result = []
        pos = 0
        while pos < n:
            w = word_choice[pos]
            if not w:
                break
            result.append(w)
            pos += len(w)

        # If we couldn't split, return the whole run as one word
        if not result:
            result = [run]

        return result

    def _get_word_score(word: str) -> float:
        """Get score for a word. Higher score = better word choice.

        Scores are based on:
        1. Word length (longer is generally better)
        2. Whether it's a common high-frequency word
        3. Penalty for very short words (1-2 chars)
        """
        if not word:
            return 0.0

        # Short words get base score
        if len(word) <= 2:
            return 1.0

        # Common high-frequency words get bonus
        if word in _HIGH_FREQ_WORDS:
            return 5.0 + len(word) * 0.5

        # Regular words get score based on length
        return 3.0 + len(word) * 0.3


# ---------------------------------------------------------------------------
# High-frequency English words that should be prioritized during word splitting.
# These are the most common words in English that often appear in glued model output.
# ---------------------------------------------------------------------------
_HIGH_FREQ_WORDS = frozenset({
    # Articles, prepositions, conjunctions
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    # Pronouns
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "its", "our", "their", "this", "that",
    "these", "those", "what", "which", "who", "whom", "whose",
    # Common verbs
    "have", "has", "had", "do", "does", "did", "will", "would", "can", "could",
    "should", "may", "might", "must", "shall", "go", "going", "gone", "come",
    "came", "get", "got", "make", "made", "take", "took", "see", "saw",
    "know", "knew", "think", "thought", "say", "said", "tell", "told",
    "ask", "asked", "try", "tried", "leave", "left", "run", "ran",
    # Common nouns
    "time", "year", "day", "week", "month", "world", "life", "hand",
    "part", "place", "case", "number", "way", "thing", "things",
    "people", "man", "woman", "child", "children", "group",
    # Common adjectives
    "good", "great", "new", "old", "big", "small", "high", "low",
    "long", "short", "early", "late", "young", "little", "own",
    "same", "different", "other", "next", "last", "first",
    # Other very common words
    "not", "no", "nor", "so", "if", "then", "than", "too", "very",
    "also", "only", "just", "still", "even", "already", "yet",
    "here", "there", "where", "when", "why", "how", "all", "each",
    "every", "both", "few", "many", "much", "some", "such",
    "about", "into", "through", "during", "before", "after",
    "because", "while", "although", "since", "unless", "however",
    "therefore", "thus", "hence", "more", "most", "less", "least",
    "better", "best", "worse", "worst",
})

# ---------------------------------------------------------------------------
# Dictionary splitter for lowercase / mixed-case glued Latin runs.
#
# The word list is a curated, intentionally small top-frequency subset
# (~1200 words) so the dictionary never produces truly absurd splits.  Words
# shorter than 2 letters (except "a", "i", and a few) are avoided — we bias
# the split towards longer words to minimise junk tokens.
# ---------------------------------------------------------------------------
_COMMON_EN_WORDS_RAW = """\
a about above across after again against ago all almost alone along already also
always am among amount an and another any anyone anything anywhere apart are
area areas around as ask asked asking asks at away back backed backing backs
be because been before began begin beginning behind being believe below between
big bill billion both brought but by called came can case cases cause caused
causes certain certainly change changed changes children city cities clear clearly
come comes common community company compared complete condition conditions continue
continued continues control could country countries couple course created day days
development did different difference difficulties do does done down during each
early earth economy education either else end ended ending ends enough entire
especially even ever every everybody everyone everything everywhere except example
executive experience fact facts fall family far fast father feel felt few fewer
field fight figures final finally find fine first five follow followed following
food for force form former forward four free friend from full further future game
gave general generally get girl give given gives go going good got great greater
group groups grow had half hand hands happen happened happens hard have having he
head health hear heard help helped helping her here herself high him himself his
history hold home hope hoped hour hours house how however huge human hundred i
idea ideas if important in include included including including indeed increase
increased individual industry instead interest into is it its itself just keep
kept kind knew know known knowledge large last late later latest law laws lay lead
leader learn learned least leave leaving left less let letter letters level life
light like line lines list little live lived living long look looked looking lose
lost lot love made main make makes man many may me mean means meant measure meet
member members men might million mind miss money month months more morning most
mother much must my myself name nation national natural near nearly necessary need
needed needs never new news next night no none nor north not note nothing now
number numbers of off offer offered office often oh oil old on once one only open
opened opinion opportunity order ordered other others our out outside over own
owned owner page paper part particular particularly parts party pass passed past
pay peace people per perhaps person personal phone physical pick picture piece
place plan plant play played player point points police policy political possible
power practice present president pressure pretty price private probably problem
problems process produce product production program project property protect proved
provide provided public put question questions quite rate rather reach read ready
real really reason reasons receive received recent recently red region relate
remember remove report represent republic require required research research resource
resources rest result results return returned right rights river road room rule
rules run running said same saw say says school science second sections see seem
seemed segment sense separate series serious several shall she short should show
shown shows side significant similar since single sister sit site six size small
social society some someone something sometimes somewhere soon sort sound sounds
source south space special specific speech spend spent spoke stage stand start
started state states stay still stop stopped story street strong structure such
suddenly suggest summer support sure surface system table take taken takes talk
talked tall tax team technology tell ten term terms test than thank that the their
them then there these they thing things think thinking this those thought three
through thus time times tire tired tires to today together told too took top toward town trade
traditional training travel tried trouble true truth try trying turn turned two
under understand unit united until up upon us use used useful uses usual usually
value various very victim view violence visit voice wait walk want wanted war was
watch water way ways we week weeks well went were west what when where whether
which while white who whole why wide wife will win wind window wish with within
without woman women won word words work working works world would write wrote year
years yes yet you young your yourself
ability accept according account achieve actually address administration admit
adopt advance advantage advice affect afford afraid again agency agent agree ahead
agree air airport all allow allows alone already alternative always among amount
analysis analyze ancient animal announce annual answer anyone anything apart
apparent apparently appeal appear appearance apple apply approach appropriate area
argue argument arrival artist article artistic arts assume attempt attend audience
author available avoid award aware balance beautiful beauty because become before
began beginning behalf behavior behind believe benefit benefits beside beyond bill
billion biological birth black blood blue board boat body book border born both
bottle bottom box brain brand break breakfast breath bridge bright bring broadcast
brother brown brush bunch budget build building built business busy buy cake call
calm camera camp campaign cancer candidate cap cap cards career carry cash catch
cause caused celebrate century certain challenge chance change chapter character
charge chart cheap check chemical chest chief child childhood choice choose church
citizen city civil claim class classic clean clear clearly clever climate clock
close clothes cloud coast code coffee cold college collection combination come
comfort command comment commercial commission commit committee common communicate
community company compare compete competition complete complex computer concept
concern conclude condition conduct conference confidence confirm conflict congress
connection consequence consider consist contain content contest context continue
contract control controversy conversation convince correct cost could country couple
courage court cover create crime crisis critical cross cultural culture cup cure
current customer cut dance danger dark data date daughter day dead deal dear death
debate debt decade decide decision declare decrease deep defeat defend defense
define degree delay deliver demand democratic demonstrate deny depart depend deputy
derive describe desert design despite detail detect determine develop device devote
diet differ difficult dinner direct director disability disagree disappear disaster
discuss display distance distinct district divide divine doctor document domestic
dominant don door double doubt draft dragon drama draw drink drive drop drug dry due
during each early ease east easy eat economic economy edge edit educate education
effect effective effort egg either election electrical electricity eliminate else
emerge emotion emotional employ employee employer empty enable encounter encourage
end endpoint enemy energy enforce engage engine engineer enhance enjoy entire
environment episode equal equip equipment equivalent error escape especially essay
essence establish estate estimate ethical evaluate evidence evolve exact example
excellent except exchange exciting executive exercise exhibit exist existence
expansion expect experience experiment expert explain exploit explore express
extend extensive extreme eye face facility fact factor factory fail fair faith
fall false familiar family famous fancy farm farmer fashion fast fatal father
fault favor fear feature federal fee feed feel female fiction field fierce fight
figure fill film final finally finance financial find fine finish fire firm first
fish fit fix flag flat flexible flight float floor flow flower fly focus follow
food foot football force foreign forest forever forget forgive formal former
formula forth fortune forum forward four fourth fox frame freedom freeze frequent
fresh friend frog from front fruit fuel full fund furniture furthermore future
gain gallery game garden gave gay generally generate generation gentle genuine
gift girl give glad glass global glory go goal going golden good got govern
government grab grace grade gradual graduate grand grant grateful grave great
green greet grey/gray grocery ground group grow guard guess guest guide guilty
habit hair half hand handle handsome hang happen happiness happy harbor hard harm
hat hate have having he heal health heart heat heaven heavy hell help her here
herself hey high highway hill him himself hip his history hit hold holiday home
honest honey hope horizon horror horse hospital host hotel hour house however
huge human humble humor hundred hunt hurry hurt husband ice idea ideal identify
ignore ill illegal illness image imagine immediately impact imply import important
impose improve impulse in inch incident include income increase indeed indicate
individual indoor industry infant influence inform information initial initiate
injury inside insight insist instance instant instead institute institution
instruction insurance intellectual intelligence intend intention interact interest
interfere internal international internet interpret into invest invite involve
iron island isolate issue item itself jacket jail jealous jeans jewel job join
joint joke journal journey joy judge jump jungle junior junk jury just justice
keen keep kept key kick kid kill kind kingdom kitchen knee knife knock know
knowledge lab/aboratory lack lady lake lamp language large last late laugh lawyer
lay lead leader leak learn leave lecture left leg legal legendary lemon lend lens
lesson let letter level lie life lifestyle lift light like likely limit line link
lion lip liquid list listen literary literature little live lively living load
loan local locate lock logical lonely long look loose lose loss lost lot love
loyal lucky lunch lung luxury machine magazine magic maid mail main maintain major
make male mall man manage manager mandate mango manner manufacture many map
march margin mark market marriage marry mask mass master match material math
matters maximum maybe mayor meal mean measure meat mechanism media medicine meet
melody memory mention mercy merge merit merry message metal meter method middle
might mile military million mind minimal minister minor minute miracle mirror
miss mission mistake mix mixed mobile mode model modify moment money monitor
monster moral morning mostly mother motion motor mount mountain mouse mouth move
movement movie much mud multiply murder muscle museum music musician must my
myself mystery myth nail naked name narrow nasty nation national native natural
nature navy near neat necessary neck need negative neighbor neither nerve net
network neutral never new news newspaper next nice night noble noise nomination
normal north notable note nothing notice novel now nuclear nude number nurse
object observe observe obtain obvious occasion occur ocean odd off offer office
official often oil okay old olive once one online only open opera operate opinion
oppose option orange orbit order ordinary organic organize origin other otherwise
ounce outdoor outer output outside oval oven overall overcome own oxygen pact page
paint pair palace pale pan panic pants paper parade parallel parasite parcel
parent park part participate particular particularly partner party pass passage
passenger passive past paste path patient pattern pause payment peace peculiar
penalty pencil penetrate perceive perfect perform perhaps period permanent permit
person persuade phase phenomenon philosopher phone photo physical pick pie piece
pile pilot pine pioneer pipe pistol pizza place plain plan planet plant plastic
plate platform play pleasant pleasure plenty pledge plug plus pocket poem poet
poetry point poison polar police policy polite political politics pollute pond pool
popular population porch pose position positive possible postpone potential power
practice praise precious predict prefer prejudice prepare presence present preserve
president pressure pretty prevent previous price pride primary priority prison
private prize probably problem procedure proceed process produce product profile
profit program project promote promise proof proper property propose prosper
protect proud provide province public punch purchase purse pure pursue push put
puzzle pyramid quality quantity queen query quest question quick quiet quit quiz
quote rabbit race radar radio raise rally ramp ranch random range rapid rarely
rate rather raw razor reach react ready reality reason rebel recall receive recent
recipe recognize recommend record recover red reflect reform refuse region regret
regular relate relation relax release relief religion rely remain remedy remind
remote remove render renew repair repeat replace reply report represent require
rescue resemble reserve reside resign resist resolve resort respect respond
response rest restore restrict result retire retreat return reveal reverse review
revolution reward rhythm rib ribbon rice rich ride ridge rifle right rigid ring
riot rip rise risk ritual rival river road roast rob robot rocket romance roof
rookie room root rope rotate rotten rough round route royal rubber rude rugby rule
ruler run rural sacred sadness safe sail salad salmon salon salt sample sand sane
satisfy sauce sausage save saw say scanner scarce scatter scene scheme scholar
science scratch screen script sea seal search season seat second secret section
sector secure see seed seek seem segment select self senate sense sentence separate
sequence series serve session set settle settle several severe shade shadow shake
shall shallow shame shape share shark sharp she sheep sheet shelf shell shelter
shift shine ship shiver shock shoot shop shore short shoulder shove show shrink
shut sick side siege sight sign signal silence silver similar simple since sincere
sing sink sister site situate six size ski skill skin skull sky slam sleep slice
slide slight slim slow small smart smell smile smoke smooth snack snake snow so
soccer social society sock soft soil solar soldier solid solution solve someone
something sometimes somewhere soon sore sort soul sound soup source south space
spare speak special specific speed spell spend spirit split spoil sponsor spoon
sport spot spray spread spring spy square stable stage stain stair start state
station statue status stay steak steal steam steel step stick still sting stock
stomach stone stop store storm story stove straight strange stranger straw stream
street stretch strike strip stroke strong structure struggle student stuff style
subject submit succeed success such sudden suffer sugar suggest suit sum summer
sun supply support suppose supreme sure surface surge surprise surrender survey
survival survive suspect sustain swallow swan swap sweat sweep sweet swim swing
sword symmetric symptom syrup system table tackle talent tank tap tape target task
taste tax teach team tear technical technique technique technology teen telephone
television tell temperature temple tenant tense term terrace territory terror
test text than thank that theater theatre theme theory therefore therapy they
thick thin thing think third this those thought threat three thrive throw thumb
thus tide tidy tie tiger tight timber time tiny tissue title toast tobacco today
toe together told tomato tomorrow ton tone tongue tonight too top topic torch
tornado tortoise toss total touch tough tour toward towards tower town toy trace
track trade tradition traffic tragic train transfer translate transport trap trash
travel treat tree tremble trial tribe trick trigger trip triumph trouble truck
true truly trust truth try tube tuition tumor tune tunnel turtle twist two type
typical ugly umbrella uncle under underground unique unit universe unknown unless
unlikely until unusual update up upon urban urge urgent use useful usually utility
vacuum vague valid valuable value vanish variety various vast vault vehicle veil
venture verify version very veteran vessel viable vibrant vicious victim victory
video view village vintage violin virtual virtue virus visa visit visual vital
vivid voice volume vote voyage wage wait wake walk wall want war wardrobe warm
warrior wash waste watch water wave wealth weapon wear weather week weird weigh
welcome welfare well were west western wet what whatever wheat wheel when where
whereas whether which while whip whisper white whole whoever why wide width wife
wild will win wind window wine wing winner winter wipe wire wisdom wise wish
witness wolf woman wonder wooden wool word work world worry worse worst worth
wrap wreck writer writing wrong yard year yellow yes yesterday yet you young your
yourself youth zero zone
hello welcome interface database assistant variety design validate behavior
select insert update delete query schema table column row join where from into
values limit order group having union index trigger procedure function view
begin commit rollback grant revoke alter drop create truncate comment grant
mysql postgresql sqlite oracle server connect connection api endpoint document
file files record records instance instances request response payload result
results summary overview contain contains containing contained container
include includes including included application applies apply applied
permission permissions latest late later connect connected connecting
assist assisted assisting assisting assist assistant containing department
departments depart assistant's here's there's let's i'd you'd we'd they'd
it's i'm you're we're they're don't can't won't isn't aren't wasn't weren't
hasn't haven't hadn't wouldn't shouldn't couldn't didn't doesn't i'll you'll
we'll they'll he'll she'll it'll that's who's what's where's when's why's
how's there'll you've we've they've i've couldn't should've would've must've
departments permissions instances records conditions applications stations
queries schemas schema functions triggers indexes procedures languages
messages methods objects projects modules systems tasks levels users
chinese japanese spanish french german latin korean vietnamese italian
portuguese dutch russian hindi arabic english
today tomorrow yesterday morning afternoon evening tonight
countryside nevertheless moreover furthermore therefore beforehand
anymore anyone anywhere everyone everyone everything everybody
nothing nobody nowhere something somebody somewhere sometimes
database databases schema endpoint endpoints payload payloads api apis
document documents file files instance instances result results
summary overview overview behavior testing write test write tests
latest tech entire ly keep things chinese french spanish german
just let me know right on it keep things in chinese keep things
lat latest latest tech and inspect inspect inspect the and inspect
queries and inspect the schema schemz schema queries
standpoint independent strategy
seems jumps makes takes works shows gives
finds calls needs keeps sees sends starts
leaves runs holds uses helps asks means
becomes remains offers allows appears expects
suggests provides creates moves lives changes
continues receives follows reaches returns
speaks reads walks writes sits stands pays
plays turns learns feels points builds
falls meets knows thinks comes goes does
has says
brown fox lazy quick dog over warm welcome
order orders user users id ids
data table tables schema schemas
file files name names page pages
type types code codes role roles
group groups field fields step steps
date dates form forms model models
value values param params input inputs
output outputs class classes line lines
item items mode modes
# Additional common words that may appear in glued model output
spaces space algorithm algorithms crucial importantly visibility
visibility visible visible visibility invisible
reward rewarding rewards rewarded
technology technological technologies technologically
everyday everyday weekend weekday midnight
specific specifically particular particularly general generally
different difference differently similar similarly
important importance importantly significant significantly
necessary necessarily sufficient sufficiently adequate adequately
available availability unavailable inaccessible
possible possibility possibly impossible
likely unlikely maybe perhaps probably certainly definitely
natural naturally artificial artificially
actual actually real really truly genuinely
suddenly immediately eventually finally currently previously
originally directly exactly precisely approximately
simply merely hardly barely only just merely
sometimes often always never ever already yet still even
here there where when why how what who which that this these those
also too enough such
show shows shown shows showing
make makes made making made
different difference differently important importance importantly
experience experienced experiences experiencing suggest suggests suggested suggestion
suggestions wonder wonders wondered maybe something someone everyone
everything everywhere sometimes anything anyone nothing really reality
actually actual probably probable certainly certain definitely definite
basically basic naturally natural suddenly sudden finally final
eventual immediately immediate currently current previously previous
original originally directly direct exactly exact particularly particular
specific specifically general generally serious seriously absolutely absolute
simple simply merely hardly barely ability abilities able unable
achievement achievements achieve achieved acquiring acquire acquired
acquisition acquisitions address addresses addressed addressed
administrative administrator administrators admire admired admires admiring
admit admits admitted admit adopts adopted adopting
advance advances advanced advanced adventure adventures adventuring
advertising advertisement advertisers advisor advisory advisories
affection affectionate affect affects affected affecting
afford affords afforded affordable agency agenda agendas agent
aggressive aggressively aggression aggressively ago agonizing
agreement agreements agree agrees agreed agreeable agreed
agricultural agriculture agricultural agriculturally
altitude altitudes altimeter altogether alternate alternated alternates
alternating alternately alternative alternatives alternatively
amazingly amazing amazed amaze amazes amazed ambassador
ambiguous ambiguity ambiguously ambition ambitious ambitiousness
amendment amendments amend amended amends amending
amusing amused amuses amusement amusement amuser
analyst analysts analysis analyze analyzed analyzing analytical
analytically analytic analog analogies analogy analogous analogously
ancestor ancestors ancestral ancestor anchor anchored anchoring
anchors ancient anciently ancillary and anecdote anecdotes
anecdotal anecdotally angle angles angled angry anger
angered angers angrily angrier angriest
angrily anguish anguished angular angularly angularities
anxious anxiously anxiety anxieties anxiousness
apart apartment apartments apiece apologetic apologetically apology
apologies apologize apologized apologizing apologizes
appalling appalled appalls appeal appeals appealed appealingly
appear appears appeared appearance appearances appearing
apple apples appliance appliances applicant applications
applauded applauding applauds applause applaud
applicable applicant applicants application applications
applied apply applies applying appointed appointment appointments
appreciated appreciates appreciating appreciation appreciative
appreciatively appreciation appreciator appreciators
approach approached approaches approaching appropriated
appropriately appropriateness appropriation appropriations
approval approvals approve approved approver approvers
approvingly approver approvers approves
approximate approximately approximation approximations
approximated approximates approximating
arbitrary arbitrarily arbitrariness arbitration arbitrators
arbitrator arbitrators arc arced arches archaic archaically
architecture architecturally architectural architectures
architect architectures archive archives archival archived archiving
arctic arctically ardent ardently ardor ardorous
arduously arduous arduously are area areas arena arenas
argued argues arguing argument arguments argumentation
argumentative argumentatively argumentum arguments
arise arises arose arisen arising
arrangement arrangements arrange arranged arranger arrangers
arranging arranges arranged arrangement arrangements
array arrays arrayed arraying arrays
arrest arrested arrests arresting arrestingly
arrival arrivals arrive arrived arrives arriving
arrogant arrogantly arrogance arrogate arrogated arrogates
arrogating arrogance arrogantly arrogance arrogantness
articulate articulated articulates articulating articulation
articulations articulately artifact artifacts artifact
artificial artificially artificiality artistic artistically
artistry artistry artist artisans artisan
artisan artists artworks artwork artwork art
articles article articled articling articled
artless artlessly artlessness artsy artsier artsiest
artistically artisticness artistry artworks
artwork artworks artwork artful artfully artfulness
arthritis arthritic arthritically
articulation articulations articulatory articulator
articulated articulates articulating articulately
articulate articulating articulate articulateness
artifact artifacts artifact artifact
artfully artfulness artful artfully
artfulness artful artless artlessly artlessness
artlessly artlessness artless
artwork artworks artwork
# Additional high-frequency words for space restoration
paragraph paragraphing paragraphs sentence sentences sent
possibility possibilities possible possibly conversation conversations conversational
exists existed existing exist becoming became become becomes
quickly quick quicker quickest curious curiosity curiousness
daily basis bases based topic topics cover covers covered covering
keep keeps kept keeping things thing think thinks thought
# Progress and related forms
progress progressing progressed progresses progression progressive progressively
# Other common verbs and their forms
improve improving improved improves improvement
report reporting reported reports reporter
work working worked works worker workers
develop developing developed develops development developer
analyze analyzing analyzed analyzes analysis analytical analytically
implement implementing implemented implements implementation
evaluate evaluating evaluated evaluates evaluation
communicate communicating communicated communicates communication
demonstrate demonstrating demonstrated demonstrates demonstration
investigate investigating investigated investigates investigation
summarize summarizing summarized summarizes summary summarization
organize organizing organized organizes organization organizer
recognize recognizing recognized recognizes recognition
connect connecting connected connects connection connectedness
reduce reducing reduced reduces reduction
increase increasing increased increases increment incrementally
solve solving solved solves solution solver
achieve achieving achieved achieves achievement
maintain maintaining maintained maintains maintenance
monitor monitoring monitored monitors monitor
manage managing managed manages management manager
# ===== Critical missing base words =====
comfortable comfortably uncomfortable discomfort
kindness kindnesses sadness happiness darkness weakness awareness
act acts acting action actions actor actress active activity react reacted reaction
stay stays stayed staying
one ones once only onto
willing willingness willingly
adapt adapting adapted adapts adaptive adaptation
procrastinate procrastinated procrastinates procrastination
reflect reflecting reflected reflects reflection reflections reflective
meaning meaninglessly meaningless meaningful meanings
surprise surprised surprising surprises surprisingly
kind kinder kindest kindly
act acts acting active activity
chose chosen choose chooses choice
fresh freshness freshly
page paged paging pagination
reflect reflection
quiet quietly quietness
chance chances chanced
moment moments
remain remained remaining remains
best better
growth grown grew grow growing
glad gladly gladness gladder gladdest
chance
tonight today tomorrow morning mornings afternoon evenings
frequently frequency frequent infrequent
actual actually
fill fills filled filling filler
full fully fuller fullest fullness fullfil fulfilled fulfills fulfillment
success successful successfully succeed succeeded succeeding succeeds success
opportunity opportunities opportunity
sometimes always never often seldom
# Common gerund/progressive forms (ing-ending words)
exploring rewarding learning creating making taking getting giving
finding knowing thinking seeing wanting using trying asking needing
leaving putting meaning letting beginning seeming helping showing
hearing playing running moving living believing bringing happening
writing sitting standing losing paying meeting including continuing
setting drawing understanding opening walking talking sleeping eating
drinking reading spending growing offering holding winning buying waiting
sending building staying falling cutting reaching killing remaining
suggesting raising passing selling requiring reporting producing expecting
receiving agreeing appearing defending pushing pulling improving missing
distinguishing organizing gathering saving catching wishing feeling fighting
allowing replacing driving employing establishing playing considering
preparing weighing teaching suggesting reading writing speaking hearing
progressing improving reporting working developing analyzing implementing
evaluating communicating demonstrating investigating summarizing organizing
recognize connecting reducing increasing solving achieving maintaining
monitoring managing reflecting adapting procrastinating
# Other common words that might appear in glued model output
visible visibility invisible clearly correctly exactly precisely
technology technological technologies system systems
everyday everyday weekend weekday midnight
specific specifically particular particularly general generally
different difference differently similar similarly
important importance importantly significant significantly
necessary necessarily sufficient sufficiently adequate adequately
available availability unavailable accessible
possible possibility possibly impossible
likely unlikely maybe perhaps probably certainly definitely
natural naturally artificial artificially
actual actually real really truly genuinely
suddenly immediately eventually finally currently previously
originally directly exactly precisely approximately
simply merely hardly barely only just merely
sometimes often always never ever already yet still even
here there where when why how what who which that this these those
also too enough such
show shows shown shows showing
make makes made making made
different difference differently important importance importantly
experience experienced experiences experiencing suggest suggests suggested suggestion
suggestions wonder wonders wondered maybe something someone everyone
everything everywhere sometimes anything anyone nothing really reality
actually actual probably probable certainly certain definitely definite
basically basic naturally natural suddenly sudden finally final
eventual immediately immediate currently current previously previous
original originally directly direct exactly exact particularly particular
specific specifically general generally serious seriously absolutely absolute
simple simply merely hardly barely ability abilities able unable
achievement achievements achieve achieved acquiring acquire acquired
acquisition acquisitions address addresses addressed addressed
administrative administrator administrators admire admired admires admiring
admit admits admitted admitted adopt adopts adopted adopting
advance advances advanced advanced adventure adventures adventuring
advertising advertisement advertisers advisor advisory advisories
affection affectionate affect affects affected affecting
afford affords afforded affordable agency agenda agendas agent
aggressive aggressively aggression aggressively ago agonizing
agreement agreements agree agrees agreed agreeable agreed
agricultural agriculture agricultural agriculturally
altitude altitudes altimeter altogether alternate alternated alternates
alternating alternately alternative alternatives alternatively
amazingly amazing amazed amaze amazes amazed ambassador
ambiguous ambiguity ambiguously ambition ambitious ambitiousness
amendment amendments amend amended amends amending
amusing amused amuses amusement amusement amuser
analyst analysts analysis analyze analyzed analyzing analytical
analytically analytic analog analogies analogy analogous analogously
ancestor ancestors ancestral ancestor anchor anchored anchoring
anchors ancient anciently ancillary and anecdote anecdotes
anecdotal anecdotally angle angles angled angry anger
angered angers angrily angrier angriest
angrily anguish anguished angular angularly angularities
anxious anxiously anxiety anxieties anxiousness
apart apartment apartments apiece apologetic apologetically apology
apologies apologize apologized apologizing apologizes
appalling appalled appalls appeal appeals appealed appealingly
appear appears appeared appearance appearances appearing
apple apples appliance appliances applicant applications
applauded applauding applauds applause applaud
applicable applicant applicants application applications
applied apply applies applying appointed appointment appointments
appreciated appreciates appreciating appreciation appreciative
appreciatively appreciation appreciator appreciators
approach approached approaches approaching appropriated
appropriately appropriateness appropriation appropriations
approval approvals approve approved approver approvers
approvingly approver approvers approves
approximate approximately approximation approximations
approximated approximates approximating
arbitrary arbitrarily arbitrariness arbitration arbitrators
arbitrator arbitrators arc arced arches archaic archaically
architecture architecturally architectural architectures
architect architectures archive archives archival archived archiving
arctic arctically ardent ardently ardor ardorous
arduously arduous arduously are area areas arena arenas
argued argues arguing argument arguments argumentation
argumentative argumentatively argumentum arguments
arise arises arose arisen arising
arrangement arrangements arrange arranged arranger arrangers
arranging arranges arranged arrangement arrangements
array arrays arrayed arraying arrays
arrest arrested arrests arresting arresting arrestingly
arrival arrivals arrive arrived arrives arriving
arrogant arrogantly arrogance arrogate arrogated arrogates
arrogating arrogance arrogantly arrogance arrogantness
articulate articulated articulates articulating articulation
articulations articulately artifact artifacts artifact
artificial artificially artificiality artistic artistically
artistry artistry artist artisans artisan
artisan artists artworks artwork artwork art
articles article articled articling articled
artless artlessly artlessness artsy artsier artsiest
artistically artisticness artistry artworks
artwork artworks artwork artful artfully artfulness
arthritis arthritic arthritically
articulation articulations articulatory articulator
articulated articulates articulating articulately
articulate articulating articulate articulateness
artifact artifacts artifact artifact
artfully artfulness artful artfully
artfulness artful artless artlessly artlessness
artlessly artlessness artless
artwork artworks artwork
# ============================================================
# Round 4 — highly specific high-frequency glued words that
# appeared in real LLM output and were NOT split before.
# ============================================================
# Daily conversation / self-introduction openings
sure hello greet greetings greeting introduce introduction myself
ourselves themselves yourselves himself herself yourself
# Sentence starters that glue to the following word
surely certainly definitely absolutely honestly frankly basically
# Three-word glued chains: "Every day is a fresh page"
every day is a fresh page new beginning bright start newday
# — note: "everyday" (adj: ordinary) is already a dictionary word;
# but in glued output "Everydayisafreshpage" the intended reading
# is "Every day is a fresh page".  Because we trust the dictionary
# greedily the first time, we pre-split at CamelCase which turns
# "Everydayisafreshpage" → "Every dayisafreshpage", and then the
# matcher needs "day" + "is" + "a" + "fresh" + "page" all to be
# found (they are).  So CamelCase pre-split alone handles it.
# But we also need the short forms:
day is was has had are were been being
# Words from the "small acts of kindness" example
ever small act acts kindness kind or a moment of quiet
quiet reflection reflections reflect reflective meaningful
# Words from the "and a short paragraph about change" example
and short paragraph paragraphs topic topics certain or keep
it general let me know and adjust just for you
# Technology / adaptation related
remaining remain remains will learn adapt give ourselves
the best chance chances to grow through new circumstances
rather than simply enduring them
# More 3-letter glue words that often appear concatenated
not lot get bit own try set way far yet big
# All forms of "would you like / I'd like" (contraction splits off)
would like certain topic or keep general adjust
# Other everyday high-frequency vocabulary
before after because between without within through across along
around behind below beside beyond during except above among against
around toward towards forward backward backwards aboard about above
# Verbs that frequently follow nouns
make take give tell speak talk say ask hear listen look feel try
love hate like love need want buy sell bring carry hold find keep
# "By remaining willing to..." — "by" is 2-letter but we want to
# anchor it when flanked.  Already covered via 2-letter function
# words list above.
remaining remain willing chance grow growth circumstance
through rather simply endure enduring them
# "Letmeknow..." — let, me, know are all there but CamelCase is
# missing because it's all lowercase.  So we need a lower-case
# 2-letter pass which IS implemented via Pass 2.
#
# NOTE: the following paragraph was PREVIOUSLY written as a bare
# (unhashed) English comment inside the raw word-string, which caused
# its very first capitalised token — "Letmeknow" — to be split() out
# as a pure-letter 9-character token and inserted into _EN_WORD_SET.
# That, in turn, made the splitter treat the glued phrase "Letmeknow"
# as an exact dictionary hit and refuse to split it.  We now prefix
# every explanatory sentence with '#' so only intentional vocabulary
# tokens reach the dictionary.  —
#   "Problem: Let-me-know starts with L (boundary-less).  'let' is
#    3 letters, then 'me' 2 letters, then 'know' 4 letters.
#    But the splitter's tail safety check for 'let' at position 0
#    needs 'me-know' to have safe start, i.e. 'me' (2-letter glue) +
#    'know' (4) must pass.  It should."
# Real vocabulary tokens added by that earlier note:
adjust adjusting adjusted adjusts adjustment adjustable
# "I'll" (contraction) — apostrophe causes a split already because
# the apostrophe run is alpha-only split but includes "'".  Good.
# "CertainTopic" — CamelCase pre-split will handle.
#
# NOTE: previously there was a plain-English comment block here:
#   "certain keep it general.  Note: the individual short words
#    "let", "me", "know", "would", "you", etc. are already listed
#    individually above; do NOT re-list them here as space-free
#    concatenations (that would poison the dictionary by making
#    "letmeknow" etc. a single word token)."
# The "certain keep it general" part at the start was intentionally
# valid word tokens, but they are duplicated below so removing the
# bare sentence is safe and keeps the dictionary clean.
certain keep it general topic would like
# Some frequent 2&3 word glued combos that get missed.
#
# The following test-note line used to read:
#   "readya reafy — nope.  "readytobefilled" handled: ready+to+be+filled."
# "readya" / "reafy" are nonsense tokens and "nope" / "handled" are
# commentary words, not vocabulary we actually want.  The real intent
# ("readytobefilled" breaks apart as ready+to+be+filled) is already
# true because all four words are present in earlier lines; no new
# tokens are needed.
#
# Adding remaining frequently used words to strengthen dictionary.
universe universal universally idea ideal ideas issue issues simple
complex complexity simply complex complicated simplify
strategy strategic strategically tactic tactics tactics
method methods technique techniques skill skills talent talents
factor factors feature features element elements aspect aspects
phase phases stage stages step steps degree degrees level levels
standard standards criteria criterion norm norms rule rules
principle principles concept concepts theory theories model models
framework architecture infrastructure foundation base bases core
# Common glued business/conversational phrases
basically honestly frankly actually literally effectively practically
theoretically fundamentally inherently essentially specifically
generally individually collectively respectively alternatively
# "In life and in technology like"
life tech like likes liked likely unlike alike likelihood
# "By remaining willing to learn and adapt"
willingly willingness willingness remain remaining
# "give ourselves the best chance to grow"
ourselves yourselves themselves himself herself myself yourself
chance chance chance chance chance best chance grow through new
circumstance circumstances
# "rather than simply enduring them"
rather instead prefer prefer preference instead simply endure
enduring endurance them they their theirs there
# More adjectives / adverbs to catch "short paragraph"
short long tall wide narrow deep shallow thick thin brief
extended prolonged concise detailed extensive comprehensive
abundant scarce sparse numerous various diverse different
paragraph paragraph paragraphs passage passages phrase phrases
# "kindness or a moment"
moment moments instant instants second seconds minute minutes hour
# "about change"
concerning regarding approximately approximately relating related
change changed changing changes unchanged changable unchanging
# Comfort-related
comfort comforts comforted comforting discomfort discomforts
# Frequent business / AI vocab
generate generating generated generation generates generative
creative creativity created creates creating creator
response responses respond responded responding reply replies
request requests requested requesting answer answers ask asks
question questions query queries issue issues problem problems
resolve resolution resolves resolved solving solved solution
# Ensure "ask", "tell", "speak" etc. have proper forms
ask asks asking asked tell tells telling told speak speaks speaking
spoke spoken speech talk talks talking talked say says saying said
# More common vocabulary from conversation
book books pen paper chair table window door floor wall
food water drink sleep walk run jump sit stand lay lie
# "and" + function word chains (e.g. andI'm andit andthe etc.)
# are handled by the splitter.
# Ensure "whether", "whether" for the "or keep it general"
whether either neither
# For "I'll" → apostrophe is included inside alpha apostrophe run
# which treats it as one run: "I'll".  That's fine since the
# dictionary already has "i'll".  Good.
# Final set of really common short words that still fail to match
put sit lie lay stand sat laid lied lain stood
# Compound words
everyone anybody someone everyone everything somebody
something nothing anything anywhere everywhere somewhere nowhere
# Make sure common verbs/nouns for daily conversation:
wash washing washed washes clean cleaning cleaned cleans cook
cooking cooked cooks drink drinking drank drunk drinks eat
eating ate eaten feed feeding fed feeds
# "make a day meaningful"
meaningful meaningfulness meaningless meaninglessly
make making made makes maker
# "small acts of"
small smaller smallest large larger largest big bigger biggest
act acts acting action actions actor actress react reacted reacting
# "quiet reflection"
quiet quieter quietest quietness quietly noisy noisier noisiest
reflect reflection reflections reflective reflector
# "short paragraph about change"
about above below under over through within without between among
# "comfortable but it is often necessary"
often seldom rarely frequently usually normally generally commonly
necessary necessity necessarily unnecessary unnecessarily
# "the things that stay still are soon the ones left behind"
things stuff belongings item items object objects still motionless
soon shortly immediately presently eventually later earlier
before after behind ahead beside next previous last next
ones left right behind forwards backwards onward
# "By remaining willing to learn and adapt"
adaptable adaptation adaptive adapted adapting adapts
learn learner learning learned learnt learns teach teacher
teaching taught teaches educate education educated educates
# "give ourselves the best chance"
chance luck opportunity opportunities fortune fortunate
# "to grow through new circumstances"
grow growing grew grown growth increase rising rise rose risen
circumstance context environment setting situation scenario case
# "rather than simply enduring"
endure tolerable tolerated tolerating tolerate bear bearing borne born
suffer suffering suffered suffers withstand withstood resisting
resist resistance resisted resisting
# "Would you like a certain topic, or keep it general?"
topic subject theme title heading headline label tag category
definite certain specific particular special general universal
broad narrow wide-ranging wide ranging overall
# "Let me know and I'll adjust for you!"
adjust tune tweak modify modifying modified modifies refine refined
refines refining alter altering altered alters change vary variant
varied varies variation customize customized customizes
# --- Additional words discovered by tail-safety debugging (2026-08-28) ---
# Without these, the _tail_has_safe_start probe returns False for otherwise
# perfectly splittable glued sentences and the whole greedy pass aborts,
# returning the original glued blob.
# Case 1: "stays till are soon..." — "till" = preposition/conjunction "until"
till until till
# Case 2: "simply [endure+ing=]enduring them" — "enduring" used standalone
endure enduring endured endures endurance
# Other frequent short prepositions / glue words missing from dictionary:
onto toward towards till unto per sans via
# --- The Little Prince / classic-literature vocabulary (2026-08-28) ---
# Missing words caused whole glued sentences to be returned unsplit because
# the greedy cursor could not form a valid boundary when it reached them.
essential essence essentially prince princes princess royal kingdom
waste wasted wastes wasting rightly rose thorn volcano sheep
# "the little prince repeated" — without 'repeated' the sentence-shape guard
# still succeeds via repeat+ed inflection, but add the flat form anyway so
# the greedy 3+ cursor can see it on first pass.
repeat repeated repeats repeating repeatedly
# --- Other frequent adjectives / nouns missing after regression probes ---
simple simpler simplest secret secrets heartily honestly vainly
# --- Common -able / -less / -like / -tion compounds whose base+suffix case(a)
# check would FAIL because they are not listed individually elsewhere but are
# extremely frequent in everyday prose (regression probes 2026-08-28):
reasonable hopeless childlike attention
breeze breezes breezy breeze breeze breezes breeze breezy breezes
carries carried carry carrying carried carries carry carried carry carries
scent scents scented scents scent scented scent scents scented scent
wildflower wildflowers wildflower wildflowers wildflowers wildflower
meadow meadows meadow meadows meadow meadows meadow meadows
excerpt excerpts excerpted excerpt excerpts excerpt excerpted excerpts excerpt
painting painted paints painting painted paints painting paints painted
shade shades shaded shading shades shade shaded shading shades shade
sets set sunset sunsets sunsets sunset sets sunsets sunset sets sunset
hills hill hilltop hills hill hilltop hills hill hilltop hills hill
gold golden gold golden gold golden gold golden gold golden gold
crimson crimson crimson crimson crimson crimson crimson crimson crimson
gentle gently gentleness gentle gently gentleness gentle gently gentleness
sky skies skies sky skies sky skies sky skies sky skies sky
classic classically classic classically classic classically classic classically
literature literary literature literary literature literary literature literary
agent agents agent agents agent agents agent agents agent agents agent
invisible visibility invisible visibility invisible visibility
essential essentially essential essentially essential essentially essential
responsible responsibility responsibly responsible responsibility responsibly
star stars starry star stars starry star stars starry
longer longest long longer longest long longer longest long
belong belonging belongs belonged belong belonging belongs belonged
planet planets planetary planet planets planetary planet planets
become becoming becomes became become becoming becomes became
remember remembers remembered remembering remember remembers remembered
beautifully beautiful beautifully beautiful beautifully beautiful
explore explores explored exploring exploration explore explores explored
theme themes themed theme themes themed theme themes themed
love loved loving loves lover love loved loving loves lover
Would could should would could should would could should
young younger youngest young younger youngest young younger youngest
prince princes princess prince princes princess prince princes
people peoples people peoples people peoples people peoples
grows grow grew grown growing grows grow grew grown growing
grown growing growth grown growing growth grown growing growth
child childhood children child childhood children child childhood
friend friends friendly friendship friend friends friendly friendship
heart hearts heartfelt heart hearts heartfelt heart hearts heartfelt
right rightly right rightly right rightly right rightly right
wrong wrongly wrong wrongly wrong wrongly wrong wrongly wrong
travel travels traveled travelling traveler travel travels traveled
travelling traveler travels travelled travellers travel travels travelled
alone lonely alone lonely alone lonely alone lonely alone lonely
tonight tonight tonight tonight tonight tonight tonight tonight tonight
tomorrow tomorrow tomorrow tomorrow tomorrow tomorrow tomorrow tomorrow
today today today today today today today today today today today today
always always always always always always always always always always
never never never never never never never never never never never never
sometimes sometimes sometimes sometimes sometimes sometimes sometimes
often often often often often often often often often often often often
usually usually usually usually usually usually usually usually
really really really really really really really really really
actually actually actually actually actually actually actually
probably probably probably probably probably probably probably
tame tamed tames taming tame tamed tames taming tame tamed tames taming
cannot can't cannot can't cannot can't cannot can't cannot can't cannot
seen seeing seen seeing seen seeing seen seeing seen seeing seen
antoine antoine antoine antoine antoine antoine antoine antoine
exupery exupery exupery exupery exupery exupery exupery exupery
saint saints saintly saint saints saintly saint saints saintly
ups ups ups ups ups ups ups ups ups ups ups ups ups ups ups
I I I I I I I I I I I I I I I
must must must must must must must must must must
endure endured endurance enduring endure endured endurance enduring
presence present presently presence present presently presence present
few fewer fewest few fewer fewest few fewer fewest few fewer fewest
caterpillar caterpillars caterpillar caterpillars caterpillar caterpillars
butterfly butterflies butterflys butterfly butterflies butterflys
claw claws claw claws claw claws claw claws claw claws claw claws
capability capabilities capability capabilities capability capabilities
information informational information informational information informational
description descriptions describe describing description descriptions
detail details detailed detail details detailed detail details detailed
touching touch touches touched touch touching touch touches touched
story stories story stories story stories story stories
fox foxes fox foxes fox foxes fox foxes
went go going gone goes went go going gone goes
mine yours ours mine yours ours mine yours ours
whom which that who whose whom which that who whose
although although although although although although although
beneath beside besides beneath beside besides beneath beside besides
throughout throughout throughout throughout throughout throughout
wish wishes wished wishing wish wishes wished wishing wish wishes
acquaint acquainted acquaintance acquaint acquainted acquaintance acquaint
can could cannot cant can could cannot cant can could cannot cant
do does did done doing do does did done doing do does did done doing
for from with without for from with without for from with without
search searches searched searching search searches searched searching
web website websites web website websites web website websites
explain explains explained explaining explanation explain explains explained
grammar grammatical grammars grammar grammatical grammars grammar grammatical
rule rules ruled ruling rule rules ruled ruling rule rules ruled ruling
logic logical logically logic logical logically logic logical logically
puzzle puzzles puzzled puzzling puzzle puzzles puzzled puzzling
suggest suggests suggested suggestion suggestions suggest suggests suggested
better best good better best good better best good better best good
phrase phrases phrased phrasing phrase phrases phrased phrasing phrase
suited suit suits suitable suit suits suitable suit suits suitable
material materials material materials material materials material
fix fixed fixes fixing fix fixed fixes fixing fix fixed fixes fixing
proof prove proven proved proves proving proof prove proven proved
read reading reads reader read reading reads reader read reading reads
write writes wrote written writing writer writes wrote written
debug debugging debugs debugged debug debugging debugs debugged debug
code coding codes coder code coding codes coder code coding codes
connect connection connections connected connecting connect connection
database databases data databases database databases data databases
fetch fetched fetching fetches fetch fetched fetching fetches fetch
retrieve retrieval retrieved retrieving retrieves retrieve retrieval
structure structured structuring structures structure structured
assist assistance assists assisted assisting assist assistance assists
image images imagine imagined imagining images image images imagine
help helped helps helping helper helped helps helping helper helped
detail details detailed detailing detail details detailed detailing
based bases basing base bases basing base bases basing base bases
generates generated generating generate generates generated generating
reference references referenced referencing reference references referenced
specific specifically specify specified specifying specific specifically
maybe maybes maybe maybes maybe maybes maybe maybes maybe maybes
social socially socialize socialization social socially socialize
media medias medium media medias medium media medias medium
post posts posted posting post posts posted posting post posts posted
trend trends trending trended trend trends trending trended trend trends
calculate calculated calculating calculation calculations calculate calculated
program programs programming programmed programmer programmers program programs
language languages languaged language languages languaged language languages
many much more most many much more most many much more most many much more
query queries queried querying query queries queried querying query queries
connected connect connection connections connected connecting connected
database databases data databases database databases data databases data
retrieve retrieval retrieved retrieving retrieves retrieve retrieval
structure structured structuring structures structure structured structuring
analysis analyze analyzed analyzing analyst analyses analysis analyze analyzed
visual visuals visual visualization visualize visuals visual visualization
content contents content contents content contents content contents
information informational information informational information informational
research researches researched researching researcher researches researched
learning learns learned learning learner learns learned learning learner
support supports supported supporting supporter supports supported supporter
draft drafts drafted drafting drafter drafts drafted drafting drafter draft
essays essay essay essays essays essay essays essay essays essays essay
poems poem poems poem poems poem poems poem poems poem poems
proof prove proven proved proves proving proof prove proven proved
improve improves improved improving improvement improves improved improving
existing exist exists existed existing existing exist exists existed
current currant currently current currant currently current currant
topics topic topic topics topics topic topic topics topics topic
summarize summarizes summarized summarizing summary summarizes summarized
translate translates translated translating translation translates translated
explain explains explained explaining explanation explain explains explained
practice practices practiced practicing practice practices practiced practice
concept concepts concept concepts concept concepts concept concepts concept
SQL sql query queries querying queried queries querying queried query
algorithms algorithm algorithm algorithms algorithm algorithms algorithm
understanding understand understood understanding understands understanding
help helped helps helping helper helped helps helping helper helped help
with within without with within without with within without with within
have has had having have has had having have has had having have has had
care cares cared caring careful carefully careless care cares cared caring
bond bonds bonded bonding bond bonds bonded bonding bond bonds bonded bonding
precious preciously precious preciously precious preciously precious
invest invested investing invests investment investments investor invest invested
built building builds builder build built building builds builder build built
forget forgets forgot forgotten forgetting forget forgets forgot forgotten forgetting
love loves loved loving lover lovers beloved love loves loved loving lover lovers
someone someones somebody some someone someones somebody some someone someones
flower flowers flowery flower flowers flowery flower flowers flowery
blossom blossoms blossoming blossom blossoms blossoming blossom blossoms blossoming
million millions millionaire million millions millionaire million millions millionaire
star stars starry stared staring star stars starry stared staring star stars starry
fox foxes foxy fox foxes foxy fox foxes foxy fox foxes foxy
teach teaches taught teaching teacher teachers teach teaches taught teaching teacher
prince princes princess princesses prince princes princess princesses prince princes
connect connection connections connected connecting connect connection connections
require requires required requiring requirement requirements require requires required
patience patient patiently impatient patience patient patiently impatient patience patient
truth true truly truths truth true truly truths truth true truly truths truth true
another other others another other others another other others another other others
just ones one once oneself just ones one once oneself just ones one once oneself
single singles singly single singles singly single singles singly single singles singly
grow grows grew grown growing grow grows grew grown growing grow grows grew grown
which that this these those which that this these those which that this these those
wasted waste wastes wasting wasted waste wastes wasting wasted waste wastes wasting
rose roses rising rise rises rose roses rising rise rises rose roses rising rise
men man mans men man mans men man mans men man mans men man mans
become becomes became becoming become becomes became becoming become becomes became
responsible responsibly responsibility responsibilities responsible responsibly
forever forevermore forever forevermore forever forevermore forever forevermore forever
get gets got gotten getting getter get gets got gotten getting getter get gets got
teaches teach taught teaching teacher teaches teach taught teaching teacher teaches
connect connection connections connected connecting connect connection connections
require requires required requiring requirement requirements require requires required

# Added 2026-08-31 for English space restoration
hears throb throbs throbbed gleam gleams gleamed dune dunes sands
reminds reminded hide hidden growup grownups looks

"""

# Build sets: full lowercase set for longest-match; also a title-case set so
# fragments like "QueryResults" that survive Pass-1 still get a second chance.
#
# IMPORTANT — the raw string contains plain-English comment lines mixed with
# word tokens (e.g. "Note: the individual short words ...").  Those comments
# MUST be filtered out otherwise punctuation-laden junk like "general.",
# '"let",', '"letmeknow..."', '================================================'
# gets absorbed into the dictionary with two catastrophic effects:
#   1. The splitter sees a glued 9-letter "letmeknow" as an exact dictionary
#      hit and refuses to split it → Letmeknow is returned unchanged.
#   2. _MAX_EN_WORD_LEN is inflated to 60 characters because of the === line,
#      so every inner scan loop runs 60 iterations instead of ~18.
# We therefore only accept tokens that consist entirely of Latin letters,
# lowercased, and clamp the max word length to a sane 18.
_EN_WORD_SET: set[str] = {
    w.lower()
    for w in _COMMON_EN_WORDS_RAW.split()
    if w.strip() and w.strip().isalpha()
}
# Maximum word length to search; used to cap inner DP lookahead.
# Clamp at 18 — English has very few words longer than that, and a clamp
# keeps the O(n * L) greedy cursor fast even if the dictionary accidentally
# grows a monster token in the future.
_MAX_EN_WORD_LEN = min(18, max((len(w) for w in _EN_WORD_SET), default=16))

# Common contractions — treated as suffixes on pronouns/auxiliary verbs.
_CONTRACTIONS = ("s", "re", "ve", "d", "ll", "m", "t")

# Two-tier ambiguous prefix guard.
#
# Tier 1 — DERIVATIONAL / INFLECTIONAL prefixes that also happen to be
#          legitimate 2-letter dictionary words (re, un, im, de, ex, bi,
#          co, en, as, at, be, by, my, so, we, on, up, me, no, go, if,
#          he).  These are ALWAYS forbidden mid-run because otherwise
#          "understand" → "un de rs t and", "assistant" → "as s is t ant".
# Tier 2 — genuine FUNCTION WORDS (in, is, it, an).  They must be
#          allowed mid-run for glued SENTENCES ("thisisatest" → "this is
#          a test") but must still be blocked when the entire fragment
#          is itself a single dictionary word (so "island", "industry",
#          "instance", "interest", "independent" don't shred).
_DICT_SPLIT_AMBIGUOUS_PREFIXES = frozenset({
    "as", "he", "at", "be", "so", "we", "on", "up", "me", "no", "go", "if",
    "my", "by", "re", "un", "im", "de", "ex", "bi", "co", "en",
})
# Function-word 2-letter tokens — only blocked when the whole fragment
# is a single dictionary word.
_DICT_SPLIT_FUNCTION_WORDS_TIER2 = frozenset({"in", "is", "it", "an"})


# Common English suffixes that should NOT be split off as standalone words.
# When the greedy dict match leaves a short suffix, we merge it back into
# the previous word rather than producing fragments like "explore in g".
_PROTECTED_SUFFIXES = frozenset({
    "ing", "ed", "ly", "er", "est", "s", "es", "en", "es",
    "tion", "sion", "ment", "ness", "ity", "able", "ible", "ful", "less",
    "ous", "ive", "al", "ence", "ance", "ure", "dom", "ship", "hood", "ism", "ist",
})


def _dict_split_latin_run(frag: str) -> str:
    """Split a single Latin run into space-separated dictionary words.

    Strategy: Greedy longest-match-first dictionary lookup with smart suffix
    handling.  When the greedy match leaves a short protected suffix (ing, ed,
    ly, ...) we merge it back into the previous word instead of producing
    junk like ``explore in g``.
    """
    if not frag:
        return frag
    has_alpha = any(("A" <= c <= "Z") or ("a" <= c <= "z") for c in frag)
    if not has_alpha or len(frag) < 5 or " " in frag:
        return frag

    low = frag.lower()
    n = len(frag)

    # If the whole fragment is a known word, keep it as-is
    if low in _EN_WORD_SET and n >= 3:
        return frag

    result_parts: list[str] = []
    i = 0
    while i < n:
        # Skip non-alpha characters
        if not (("a" <= low[i] <= "z") or low[i] == "'"):
            result_parts.append(frag[i])
            i += 1
            continue

        # Check if the remaining text from this position is a protected
        # suffix (e.g., "ing", "ed", "ly", ...).  If so, emit it as a single
        # fragment and stop — protected suffixes should never be further split.
        remaining_from_here = low[i:]
        if (len(remaining_from_here) >= 2
                and remaining_from_here in _PROTECTED_SUFFIXES):
            result_parts.append(frag[i:])
            i = n
            break

        # Try to match the longest dictionary word starting at position i.
        # Strategy: First try the longest possible match. If the full candidate
        # is a dictionary word, use it directly. Only fall back to suffix-based
        # splitting when the full candidate is NOT a dictionary word (to avoid
        # splitting "exploring" into "explor" + "ing").
        best_len = 0
        for wlen in range(min(_MAX_EN_WORD_LEN, n - i), 0, -1):
            candidate = low[i:i+wlen]
            if candidate not in _EN_WORD_SET:
                continue

            # If the full candidate word is in the dictionary, use it directly.
            # This prevents incorrect splits like "exploring" → "explor ing"
            # or "spaces" → "space s".
            best_len = wlen
            break

        if best_len > 0:
            result_parts.append(frag[i:i+best_len])
            i += best_len
        else:
            # No dictionary match found at this position, collect consecutive
            # non-dictionary characters and try heuristic splitting.
            oov_start = i
            while i < n and (("a" <= low[i] <= "z") or low[i] == "'"):
                # Check if we can form a dictionary word from this position
                found = False
                found_word_len = 0
                for wlen in range(min(_MAX_EN_WORD_LEN, n - i), 2, -1):
                    candidate = low[i:i+wlen]
                    if candidate in _EN_WORD_SET:
                        # Don't stop on ambiguous prefixes
                        if i > oov_start and candidate in _DICT_SPLIT_AMBIGUOUS_PREFIXES:
                            continue
                        found = True
                        found_word_len = wlen
                        break
                if found:
                    # We found a dictionary word at position i.
                    # First, process the OOV text collected so far.
                    oov_text = frag[oov_start:i]
                    found_word = frag[i:i+found_word_len]
                    
                    # Check if OOV text + found word forms a known word or
                    # looks like a reasonable single word. If so, merge them.
                    combined = oov_text + found_word
                    if combined.lower() in _EN_WORD_SET:
                        # Combined form is a known word, keep it as one
                        result_parts.append(combined)
                    elif len(oov_text) >= 3 and len(oov_text) <= 6 and found_word.lower() in _EN_WORD_SET:
                        # OOV text is moderate length and found word is a known word.
                        # Check if OOV text ends with a common prefix or the
                        # combination looks reasonable.
                        # If OOV text has no clear split points, merge with found word
                        oov_split = _heuristic_word_split(oov_text)
                        if len(oov_split) == 1:
                            # OOV couldn't be split, merge with found word
                            result_parts.append(combined)
                        else:
                            result_parts.extend(oov_split)
                            result_parts.append(found_word)
                    else:
                        # Normal case: process OOV text and add found word separately
                        if len(oov_text) >= 3:
                            split_result = _heuristic_word_split(oov_text)
                            result_parts.extend(split_result)
                        elif len(oov_text) > 0:
                            result_parts.append(oov_text)
                        result_parts.append(found_word)
                    i += found_word_len
                    break
                i += 1
            else:
                # We reached the end of the word without finding a dictionary word.
                # Process the remaining OOV text.
                oov_text = frag[oov_start:i]
                if len(oov_text) >= 3:
                    split_result = _heuristic_word_split(oov_text)
                    result_parts.extend(split_result)
                elif len(oov_text) > 0:
                    result_parts.append(oov_text)

    # ---- Post-processing: merge short protected suffixes ------------------
    # Pass through the result_parts and merge any short protected suffix
    # fragments (1-3 chars) back into the preceding word.  Multi-pass to
    # handle nested cases like ["explore", "in", "g"].
    for _pass in range(3):
        if len(result_parts) <= 1:
            break
        merged: list[str] = []
        j = 0
        changed = False
        while j < len(result_parts):
            part = result_parts[j]
            # Case 0: single char that is itself a protected suffix → merge into prev
            # This handles cases like "space s" where "s" is a single-char suffix
            if (len(merged) > 0
                    and len(part) == 1
                    and part.lower() in _PROTECTED_SUFFIXES):
                merged[-1] = merged[-1] + part
                changed = True
            # Case 1: part is a known protected suffix → merge into prev
            elif (len(merged) > 0
                    and len(part) >= 2
                    and part.lower() in _PROTECTED_SUFFIXES):
                merged[-1] = merged[-1] + part
                changed = True
            # Case 2: single char + next is protected suffix → merge both
            elif (len(merged) > 0
                    and len(part) == 1
                    and j + 1 < len(result_parts)
                    and len(result_parts[j + 1]) >= 2
                    and result_parts[j + 1].lower() in _PROTECTED_SUFFIXES):
                merged[-1] = merged[-1] + part + result_parts[j + 1]
                changed = True
                j += 1  # skip the suffix part
            # Case 3: two single chars that form a protected suffix
            elif (len(merged) > 0
                    and len(part) == 1
                    and j + 1 < len(result_parts)
                    and len(result_parts[j + 1]) == 1
                    and (part + result_parts[j + 1]).lower() in _PROTECTED_SUFFIXES):
                merged[-1] = merged[-1] + part + result_parts[j + 1]
                changed = True
                j += 1
            # Case 4: consecutive short fragments (<=3 chars) that could be
            # parts of a longer word. Merge them if they form a known word
            # or if the combined length is reasonable.
            # e.g., ["pro", "mi", "sing"] → ["promising"]
            elif (len(part) <= 3
                    and j + 1 < len(result_parts)
                    and len(result_parts[j + 1]) <= 3):
                # Try to merge consecutive short fragments
                combined = part
                k = j + 1
                while k < len(result_parts) and len(result_parts[k]) <= 3:
                    combined += result_parts[k]
                    k += 1
                # If the combined word is in the dictionary or is long enough,
                # merge all fragments together
                if len(combined) >= 4 and (combined.lower() in _EN_WORD_SET or len(combined) >= 6):
                    merged.append(combined)
                    changed = True
                    j = k - 1  # will be incremented below
                else:
                    merged.append(part)
            # Case 5: part is a single char and next is also single char,
            # and their combination is a protected suffix
            elif (len(merged) > 0
                    and len(part) == 1
                    and j + 1 < len(result_parts)
                    and len(result_parts[j + 1]) == 1
                    and (part + result_parts[j + 1]).lower() in _PROTECTED_SUFFIXES):
                merged[-1] = merged[-1] + part + result_parts[j + 1]
                changed = True
                j += 1
            else:
                merged.append(part)
            j += 1
        if not changed:
            break
        result_parts = merged

    return " ".join(result_parts)


# Common suffixes and prefixes for heuristic word splitting
_COMMON_EN_SUFFIXES = (
    "tion", "sion", "ment", "ness", "ity", "able", "ible", "ful", "less",
    "ous", "ive", "al", "ly", "er", "est", "ing", "ed", "es", "en",
    "ence", "ance", "ure", "dom", "ship", "hood", "ism", "ist",
    "logy", "graphy", "phobia", "ward", "wards",
)
_COMMON_EN_PREFIXES = (
    "un", "re", "pre", "dis", "mis", "over", "under", "inter",
    "trans", "super", "anti", "auto", "circum", "co", "counter",
    "de", "extra", "fore", "hyper", "macro", "micro", "mid",
    "mini", "mono", "multi", "neo", "non", "out", "peri", "poly",
    "post", "proto", "pseudo", "semi", "sub", "ultra",
)


def _heuristic_word_split(text: str) -> list[str]:
    """Split an unknown word using common English suffixes and prefixes.

    Uses iterative splitting with strict quality checks to avoid creating
    nonsensical fragments. Returns the original text if no reasonable split
    can be found.
    """
    if len(text) < 4:
        return [text]

    # First priority: try suffix splitting (e.g., "progressing" -> "progress" + "ing")
    for suffix in sorted(_COMMON_EN_SUFFIXES, key=len, reverse=True):
        if text.endswith(suffix) and len(text) > len(suffix) + 3:
            stem = text[:-len(suffix)]
            # Must have reasonable stem length (>= 4 chars)
            if len(stem) < 4 or not _has_vowel(stem):
                continue
            # For very short suffixes (<= 2 chars), stem must be a known word
            if len(suffix) <= 2:
                if stem.lower() not in _EN_WORD_SET:
                    continue
            # If stem is a known word, this is a good split
            if stem.lower() in _EN_WORD_SET:
                result = [stem, suffix]
                return _validate_and_merge_fragments(text, result)
            # For unknown stems, only split if stem is long enough (>= 6)
            # and doesn't produce problematic fragments
            if len(stem) >= 6:
                # Recursively split the stem
                sub_result = _heuristic_word_split(stem)
                # Check if sub_result is reasonable (no very short fragments)
                if sub_result and all(len(f) >= 2 for f in sub_result):
                    result = sub_result + [suffix]
                    return _validate_and_merge_fragments(text, result)

    # Second priority: try prefix splitting
    for prefix in sorted(_COMMON_EN_PREFIXES, key=len, reverse=True):
        if text.startswith(prefix) and len(text) > len(prefix) + 3:
            rest = text[len(prefix):]
            if len(rest) < 4 or not _has_vowel(rest):
                continue
            if rest.lower() in _EN_WORD_SET:
                result = [prefix, rest]
                return _validate_and_merge_fragments(text, result)
            if len(rest) >= 6:
                sub_result = _heuristic_word_split(rest)
                if sub_result and all(len(f) >= 2 for f in sub_result):
                    result = [prefix] + sub_result
                    return _validate_and_merge_fragments(text, result)

    # Third priority: try recursive splitting based on dictionary words
    # Require at least one side to be a known word and both sides to be
    # at least 4 characters to avoid bad splits
    for split_pos in range(4, len(text) - 3):
        left = text[:split_pos]
        right = text[split_pos:]
        if len(left) < 4 or len(right) < 4:
            continue

        left_is_word = left.lower() in _EN_WORD_SET
        right_is_word = right.lower() in _EN_WORD_SET
        left_has_vowel = _has_vowel(left)
        right_has_vowel = _has_vowel(right)

        # Both sides must look reasonable
        if not left_has_vowel or not right_has_vowel:
            continue

        # Require: left is a known word (>= 4) OR right is a known word (>= 4)
        # This prevents nonsensical splits like "progres" -> ["pro", "gres"]
        if (left_is_word and len(left) >= 4) or (right_is_word and len(right) >= 4):
            result = []
            if left_is_word:
                result.append(left)
            else:
                sub_left = _heuristic_word_split(left)
                if sub_left and all(len(f) >= 3 for f in sub_left):
                    result.extend(sub_left)
                else:
                    continue  # Skip this split, sub-split failed
            if right_is_word:
                result.append(right)
            else:
                sub_right = _heuristic_word_split(right)
                if sub_right and all(len(f) >= 3 for f in sub_right):
                    result.extend(sub_right)
                else:
                    continue  # Skip this split, sub-split failed
            return _validate_and_merge_fragments(text, result)

    # Last resort: return the text as-is (conservative approach)
    return [text]


def _validate_and_merge_fragments(original: str, fragments: list[str]) -> list[str]:
    """Validate that the fragments make sense and merge if needed.

    Uses multiple heuristics to detect and reject nonsensical splits.
    Returns the original text if the fragments don't look right.
    """
    if len(fragments) <= 1:
        return fragments

    # Check 1: Any fragment shorter than 2 chars is suspicious
    # (except for very common single-char words like "a", "i")
    if any(len(f) < 2 for f in fragments):
        # Find the suspicious fragment
        for i, f in enumerate(fragments):
            if len(f) < 2 and f.lower() not in _EN_WORD_SET:
                # Try to merge this fragment with neighbors
                if i > 0:
                    fragments[i-1] = fragments[i-1] + f
                    fragments.pop(i)
                    return _validate_and_merge_fragments(original, fragments)
                elif i < len(fragments) - 1:
                    fragments[i+1] = f + fragments[i+1]
                    fragments.pop(i)
                    return _validate_and_merge_fragments(original, fragments)
        # If we can't fix it, return original
        return [original]

    # Check 2: Count very short fragments (<= 2 chars)
    very_short_count = sum(1 for f in fragments if len(f) <= 2)
    total_len = sum(len(f) for f in fragments)

    # If too many very short fragments, this is likely a bad split
    if very_short_count >= len(fragments) // 2 and len(fragments) > 2:
        return [original]

    # Check 3: If the result has more than 3 fragments and the longest is < 4 chars
    if len(fragments) > 3 and max(len(f) for f in fragments) < 4:
        return [original]

    # Check 4: If all fragments are <= 3 chars and there are more than 2
    all_short = all(len(f) <= 3 for f in fragments)
    if all_short and len(fragments) > 2:
        return [original]

    # Check 5: Verify that combining fragments approximately equals original
    # (to catch cases where characters were lost)
    combined = "".join(fragments)
    if len(combined) != len(original):
        return [original]

    return fragments


def _has_vowel(text: str) -> bool:
    """Check if text contains at least one vowel."""
    return any(c in "aeiou" for c in text.lower())


# Heuristic language detection.  We deliberately avoid a runtime dependency on
# `langdetect` so it only does something simple: measure ratio of
# Latin-letter-based tokens vs CJK characters.  This is enough to decide
# whether we want to add the "English whitespace preservation" system rule.
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\U0002a700-\U0002b73f\U0002b740-\U0002b81f\U0002b820-\U0002ceaf\u3040-\u30ff\u31f0-\u31ff\ua960-\ua97f\U0001b000-\U0001b12f\u1100-\u11ff\uac00-\ud7af\ud7b0-\ud7ff\ud800-\udbff]")
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def _detect_input_is_english_like(text: str, *, latin_ratio_threshold: float = 0.55) -> bool:
    """Return True if the input is dominated by space-delimited Latin words.

    The rule is intentionally conservative for Chinese and aggressive for pure
    English.  It will also trigger for German/French/Spanish text, which is
    fine — those scripts equally need whitespace.
    """
    if not text:
        return False
    latin_letters = sum(1 for ch in text if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))
    cjk_chars = len(_CJK_RE.findall(text))
    digits = sum(1 for ch in text if ch.isdigit())
    other_graphic = sum(
        1 for ch in text if ch.isprintable() and not ch.isspace() and not ch.isascii() and not _CJK_RE.search(ch)
    )
    total_graphic = max(1, latin_letters + cjk_chars + digits + other_graphic)
    latin_ratio = latin_letters / total_graphic
    cjk_ratio = cjk_chars / total_graphic

    # ---- Conservative CJK-short-circuit gates --------------------------------
    # (a) Absolute short-circuit: if the user wrote 6 or more actual CJK
    #     ideograms / kana / hangul chars, the turn is almost certainly CJK-led
    #     regardless of any mixed-in SQL, URLs, brand names or ASCII symbol
    #     tables.  Do NOT inject so the model behaves naturally for replies.
    if cjk_chars >= 6:
        return False
    # (b) Ratio short-circuit: >= 20% CJK content still points to a CJK-led
    #     turn even with a long tail of Latin/ASCII code.
    if cjk_ratio >= 0.20:
        return False

    # Pure short English: "show me some english", "speak some english", etc.
    if cjk_chars == 0 and latin_letters >= 3:
        return True
    # Mixed turn — default gate requires 55% Latin, but if ANY CJK character
    # shows up we raise the bar dramatically (72 %) so "a few CJK words + a
    # long SQL snippet" still reads as a Chinese user who happened to paste
    # code — not someone wanting an English answer.
    threshold = latin_ratio_threshold if cjk_chars == 0 else 0.72
    if latin_ratio >= threshold:
        return True
    return False


def _message_text_content(m: Any) -> str:
    """Extract the textual content of a LangChain message, regardless of whether
    its content is a plain string or a list of multimodal blocks.
    """
    content = getattr(m, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    text_parts.append(t)
            elif hasattr(block, "text") and isinstance(block.text, str):
                text_parts.append(block.text)
        return " ".join(text_parts)
    return ""


_ENGLISH_WHITESPACE_RULE = (
    "## Mandatory Whitespace Preservation (this turn only)\n"
    "The user message for this turn is written in English.  You MUST keep a "
    "single ASCII space character between every English/Latin word.  Never "
    "concatenate English words together.  Even if you compress output, never "
    "remove spaces between English words.  This rule is strictly enforced for "
    "English/Latin text; Chinese/CJK text needs no spaces."
)


def _inject_english_whitespace_rule_if_needed(graph_input: dict) -> None:
    """If the latest human message looks English-ish, prepend a short System
    reminder that forces whitespace preservation.

    Mutates ``graph_input["messages"]`` in place.
    """
    if not isinstance(graph_input, dict):
        return
    msgs = graph_input.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return

    # Walk backwards to find the most recent human message — in a single turn
    # it's usually msgs[-1].
    last_human_idx = -1
    for idx in range(len(msgs) - 1, -1, -1):
        msg = msgs[idx]
        if getattr(msg, "type", "") == "human":
            last_human_idx = idx
            break
    if last_human_idx < 0:
        return
    last_human = msgs[last_human_idx]
    text = _message_text_content(last_human)
    if not _detect_input_is_english_like(text):
        return

    try:
        from langchain_core.messages import SystemMessage
    except Exception:  # pragma: no cover — safety net
        return

    rule_message = SystemMessage(content=_ENGLISH_WHITESPACE_RULE)
    # Insert right before the last human so it's immediately contextualised
    # against the incoming English query, but earlier system messages remain
    # untouched.
    msgs.insert(last_human_idx, rule_message)


def _clean_model_text(text: str, skip_space_restoration: bool = False) -> str:
    if not text:
        return text
    text = _SYSTEM_REMINDER_RE.sub("", text)
    text = _TOOL_CALL_SECTION_RE.sub("", text)
    text = _TOOL_CALL_BLOCK_RE.sub("", text)
    text = _TOOL_CALL_ARG_RE.sub("", text)
    text = _SINGLE_MARKER_RE.sub("", text)
    # Clean up any previously added tool call markers (from earlier code versions)
    text = _TOOL_OMISSION_NAMED_REGEX.sub("", text)  # [工具调用: name1, name2]
    text = re.sub(_TOOL_OMISSION_MARKER_RE, "", text)   # [工具调用已省略]
    if not skip_space_restoration:
        original_text = text
        text = _restore_english_spaces(text)
        if text != original_text:
            logger.info("English spaces restored: len %d -> %d (sample: %r -> %r)",
                        len(original_text), len(text),
                        original_text[:80], text[:80])
    text = text.strip()
    return text


def _extract_text_from_chunk(chunk: Any) -> str:
    """Extract text content from a streaming AIMessageChunk.

    Args:
        chunk: A streaming chunk (AIMessageChunk or tuple)

    Returns:
        The text content of the chunk, or empty string if not a text chunk
    """
    try:
        # Handle tuple chunks (messages mode yields (chunk, metadata) tuples)
        if isinstance(chunk, tuple) and len(chunk) >= 1:
            chunk_obj = chunk[0]
        else:
            chunk_obj = chunk

        # Check if it's an AI message chunk
        if hasattr(chunk_obj, 'type'):
            msg_type = getattr(chunk_obj, 'type', '')
            if msg_type not in ('ai', 'assistant', 'AIMessage', 'AIMessageChunk'):
                return ""

        # Extract content
        content = getattr(chunk_obj, 'content', None)
        if content is None:
            return ""

        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            # For multimodal content, extract text blocks
            text_parts = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and item.get('type') == 'text':
                    text_parts.append(item.get('text', ''))
            return "".join(text_parts)
        return ""
    except Exception:
        return ""


def _clean_aimessage_content(obj: Any, is_streaming_chunk: bool = False, skip_space_restoration: bool = False) -> Any:
    """Recursively strip raw tool-call markers from AIMessage/AIMessageChunk content.
    
    Args:
        obj: The message object to clean
        is_streaming_chunk: If True, this is a streaming AIMessageChunk. 
            In streaming mode, do NOT add [工具调用已省略] markers to empty chunks,
            as these markers will be accumulated into the final AI message content.
            Markers should only be added to the final complete AIMessage in values mode.
        skip_space_restoration: If True, skip English space restoration (for streaming chunks
            where individual chunks are too small for correct restoration).
    """
    if obj is None:
        return None
    if isinstance(obj, str):
        return _clean_model_text(obj, skip_space_restoration=skip_space_restoration)
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if k == "content" and isinstance(v, str):
                cleaned[k] = _clean_model_text(v, skip_space_restoration=skip_space_restoration)
            elif k == "content" and isinstance(v, list):
                cleaned[k] = [
                    _clean_model_text(item, skip_space_restoration=skip_space_restoration) if isinstance(item, str) else item
                    for item in v
                ]
            else:
                cleaned[k] = _clean_aimessage_content(v, is_streaming_chunk=is_streaming_chunk, skip_space_restoration=skip_space_restoration)
        return cleaned
    if isinstance(obj, (list, tuple)):
        return [_clean_aimessage_content(item, is_streaming_chunk=is_streaming_chunk, skip_space_restoration=skip_space_restoration) for item in obj]
    if hasattr(obj, "content"):
        try:
            msg_type = getattr(obj, "type", "unknown")
            msg_id = getattr(obj, "id", None) or getattr(obj, "lc_id", None) or f"id_{id(obj)}"
            content = obj.content
            # Extract tool names BEFORE cleaning (from raw text markers)
            raw_text = ""
            if isinstance(content, str):
                raw_text = content
            elif isinstance(content, list):
                raw_text = "".join(item for item in content if isinstance(item, str))
            extracted_names = _extract_tool_names_from_text(raw_text)

            if isinstance(content, str):
                obj.content = _clean_model_text(content, skip_space_restoration=skip_space_restoration)
            elif isinstance(content, list):
                obj.content = [
                    _clean_model_text(item, skip_space_restoration=skip_space_restoration) if isinstance(item, str) else item
                    for item in content
                ]
            if isinstance(obj.content, str):
                cleaned_content = obj.content
            elif isinstance(obj.content, list):
                cleaned_content = "".join(
                    item for item in obj.content if isinstance(item, str)
                )
            else:
                cleaned_content = ""

            # Collect tool names from all sources
            tc_names = list(extracted_names)
            # Also try tool_calls/tool_call_chunks attributes
            tool_calls_list = getattr(obj, "tool_calls", None)
            tool_call_chunks_list = getattr(obj, "tool_call_chunks", None)
            
            if tool_calls_list:
                for tc in tool_calls_list:
                    name = _tool_call_display_name(tc)
                    if name and name not in tc_names:
                        tc_names.append(name)
            if tool_call_chunks_list:
                for tcc in tool_call_chunks_list:
                    name = _tool_call_display_name(tcc)
                    if name and name not in tc_names:
                        tc_names.append(name)

            # If there are tool calls, always add markers with names
            if tc_names:
                marker = "[工具调用: " + ", ".join(tc_names) + "]"
                if isinstance(obj.content, str):
                    current_content = obj.content
                    if current_content.strip():
                        # Append marker to existing content
                        obj.content = current_content + "\n" + marker
                    else:
                        # Replace empty content with marker
                        obj.content = marker
                elif isinstance(obj.content, list):
                    # For list-type content, append a text block with the marker
                    has_text = False
                    for item in obj.content:
                        if isinstance(item, str) and item.strip():
                            has_text = True
                            break
                        elif isinstance(item, dict) and item.get("text", "").strip():
                            has_text = True
                            break
                    if has_text:
                        obj.content = obj.content + [{"type": "text", "text": marker}]
                    else:
                        obj.content = [{"type": "text", "text": marker}]
            elif not cleaned_content.strip() and (tool_calls_list or tool_call_chunks_list):
                # Only add the generic marker when the message actually has
                # tool_calls/tool_call_chunks but we couldn't extract names.
                # This prevents pure-text AI messages from getting the marker.
                # In streaming mode, do NOT add generic markers to empty chunks.
                # These markers would be accumulated into the final AI message content,
                # causing duplicate tool call displays.
                if not is_streaming_chunk:
                    obj.content = "[工具调用已省略]"
                else:
                    # In streaming mode, keep empty chunks empty
                    obj.content = "" if isinstance(obj.content, str) else obj.content
        except Exception:
            logger.exception("Exception in _clean_aimessage_content")
    if hasattr(obj, "additional_kwargs") and isinstance(obj.additional_kwargs, dict):
        if "reasoning_content" in obj.additional_kwargs:
            rc = obj.additional_kwargs["reasoning_content"]
            if isinstance(rc, str):
                obj.additional_kwargs["reasoning_content"] = _clean_model_text(rc)
    return obj


def _pick_default_prompt(non_text_types: set) -> str:
    """Pick a type-appropriate Chinese default prompt for media-only messages.

    When a message carries only non-text parts (images, files, video,
    audio) and no text block, providers like Kimi require at least one
    non-empty text block.  The injected prompt doubles as a useful
    instruction rather than a cryptic placeholder.

    Priority order: image > file > video > audio > generic fallback.
    """
    type_prompts = {
        "image_url": "请分析这张图片",
        "image": "请分析这张图片",
        "file_path": "请阅读这个文件",
        "video_url": "请分析这个视频",
        "video": "请分析这个视频",
        "audio_url": "请分析这段音频",
        "audio": "请分析这段音频",
    }
    # Check in priority order
    for key in ["image_url", "image", "file_path", "video_url", "video", "audio_url", "audio"]:
        if key in non_text_types:
            return type_prompts[key]
    # Mixed / unknown types
    return "请分析附件内容"


def _normalize_human_multimodal_content_for_provider(message: Any) -> Any:
    """Fix up a human message's multimodal ``content`` before sending to a provider.

    History human messages often carry content like::

        [
            {"type": "image_url", "image_url": {"url": "..."}},
            {"type": "text", "text": ""},   # <-- empty text block
        ]

    This happens naturally for pure-picture sends (the text block is still
    inserted as a stable content envelope) or after stripping upload tags
    from a text body.  Providers such as Kimi/Moonshot treat an empty
    ``text`` block as a hard 400 ``text content is empty`` error, even
    though the non-text parts are perfectly valid.  They also sometimes
    require **at least one** text block — a pure-image array is rejected
    too.

    This helper therefore normalizes every ``HumanMessage`` / message with
    ``type == "human"`` that carries list content by:

    1. Dropping every ``{"type": "text", ...}`` block whose ``text`` is
       blank / whitespace-only after stripping.
    2. If only non-text parts (images, files) remain, inserting a tiny
       neutral text placeholder ``"(图片附件)"`` so providers that demand
       at least some text still accept the payload.
    3. If the result collapses to exactly one text block, unwrapping it
       back to a plain Python string (some providers handle string vs
       array subtly differently).

    The function is a no-op for non-human messages and for messages whose
    ``content`` is already a string / scalar, and **never** mutates the
    persisted checkpoint state.
    """
    if getattr(message, "type", "") != "human":
        return message
    original = getattr(message, "content", None)
    if not isinstance(original, list):
        return message
    # 1) Drop every {"type": "text", ...} block whose text is blank /
    #    whitespace-only after stripping.
    # 2) Collect non-text part types to pick a type-appropriate default prompt.
    filtered: list[Any] = []
    non_text_types: set = set()
    for part in original:
        if not isinstance(part, dict):
            filtered.append(part)
            continue
        part_type = part.get("type")
        if part_type == "text":
            text_val = part.get("text", "") or ""
            if isinstance(text_val, str) and text_val.strip() != "":
                filtered.append(part)
        else:
            non_text_types.add(part_type)
            filtered.append(part)

    # 3) If only non-text parts remain, inject a type-appropriate Chinese
    #    prompt so providers that demand at least some text accept the
    #    payload.  Covers image / file / video / audio uploads.
    if non_text_types and not any(
        isinstance(p, dict) and p.get("type") == "text" for p in filtered
    ):
        prompt = _pick_default_prompt(non_text_types)
        filtered.insert(0, {"type": "text", "text": prompt})
    if (
        len(filtered) == 1
        and isinstance(filtered[0], dict)
        and filtered[0].get("type") == "text"
    ):
        new_content: Any = filtered[0].get("text", "") or ""
    else:
        new_content = filtered
    if new_content == original:
        return message
    try:
        copied = message.model_copy(update={"content": new_content})
        object.__setattr__(copied, "__deerflow_normalized__", True)
        return copied
    except Exception:
        try:
            message.content = new_content  # type: ignore[attr-defined]
            object.__setattr__(message, "__deerflow_normalized__", True)
        except Exception:
            pass
    return message


_TOOL_OMISSION_MARKER = "[工具调用已省略]"
_TOOL_OMISSION_MARKER_RE = re.escape(_TOOL_OMISSION_MARKER)  # for re.sub
_TOOL_OMISSION_NAMED_REGEX = re.compile(r"\[工具调用:[^\]]*\]")  # [工具调用: name1, name2]

# run_id -> {ai_msg_id: [tool_names]} — shared between values-stream and messages-stream
_VALUES_AI_TOOL_CACHE: dict[str, dict[str, list[str]]] = {}


def _cache_ai_tool_names_from_values(run_id: str, state_messages: Any) -> None:
    """Scan a channel-values messages list and record AI message id → tool names.

    Supports both LangChain objects (before serialization) and plain dicts
    (after serialization).  The cache is consumed by
    :func:`_enrich_tool_call_content_in_serialized` to back-fill the tool-name
    markers into streamed AIMessageChunks that do not carry tool_calls.
    """
    if not isinstance(state_messages, (list, tuple)):
        return
    cache = _VALUES_AI_TOOL_CACHE.setdefault(run_id, {})
    for m in state_messages:
        # Determine message type — dict or object.
        if isinstance(m, dict):
            msg_type = m.get("type", "")
            msg_id = m.get("id")
            tcs = m.get("tool_calls")
            tccs = m.get("tool_call_chunks")
        else:
            msg_type = getattr(m, "type", "")
            msg_id = getattr(m, "id", None)
            if not isinstance(msg_id, str):
                msg_id = getattr(m, "lc_id", None) or f"__objid_{id(m)}"
            tcs = getattr(m, "tool_calls", None)
            tccs = getattr(m, "tool_call_chunks", None)
        if msg_type not in ("ai", "AIMessage", "AIMessageChunk"):
            continue
        names: list[str] = []
        if isinstance(tcs, (list, tuple)):
            for tc in tcs:
                n = _tool_call_display_name(tc)
                if n:
                    names.append(n)
        if isinstance(tccs, (list, tuple)):
            for tcc in tccs:
                n = _tool_call_display_name(tcc)
                if n and n not in names:
                    names.append(n)
        if names:
            cache[str(msg_id)] = names


def _drop_values_cache(run_id: str) -> None:
    _VALUES_AI_TOOL_CACHE.pop(run_id, None)


def _patch_one_message_content(message: Any, names: list[str], existing_text: str = "") -> str:
    """Return the replacement content string for an AI message.

    If ``existing_text`` already contains named markers like ``[工具调用: a, b]``
    we keep them untouched.  Otherwise we either insert a named marker from
    ``names`` or the generic ``[工具调用已省略]`` placeholder.
    """
    if not names:
        if not existing_text.strip() or existing_text.strip() == _TOOL_OMISSION_MARKER:
            return _TOOL_OMISSION_MARKER
        return existing_text
    marker = "[工具调用: " + ", ".join(names) + "]"
    # If content already has this specific named marker, don't duplicate.
    if marker in existing_text:
        return existing_text
    # Replace any generic omission marker or empty content with the named one.
    stripped = existing_text.strip()
    if not stripped or stripped == _TOOL_OMISSION_MARKER:
        return marker
    # Fallback: prepend the marker so detectToolOmissions() still parses it.
    return marker + "\n" + existing_text


def _cache_and_patch_values_messages(run_id: str, messages: list[Any]) -> None:
    """Fill the values cache AND in-place patch each AI message's content.

    Works on both raw LangChain message objects (``chunk`` in worker) and
    plain dict messages (serialized responses).  After this call the messages
    list can be redacted/serialized/published and clients will see the
    tool-name markers.

    NOTE: Only the LAST (newest) AI message gets space restoration applied.
    Historical messages are left untouched to avoid re-processing already
    displayed content.
    """
    if not isinstance(messages, list):
        return
    cache = _VALUES_AI_TOOL_CACHE.setdefault(run_id, {})

    # Find the index of the last AI message
    last_ai_idx = -1
    for idx, m in enumerate(messages):
        if isinstance(m, dict):
            msg_type = m.get("type", "")
            if msg_type in ("ai", "AIMessage", "AIMessageChunk"):
                last_ai_idx = idx
        else:
            msg_type = getattr(m, "type", "")
            if msg_type in ("ai", "AIMessage", "AIMessageChunk"):
                last_ai_idx = idx

    for idx, m in enumerate(messages):
        if isinstance(m, dict):
            msg_type = m.get("type", "")
            if msg_type not in ("ai", "AIMessage", "AIMessageChunk"):
                continue
            msg_id = m.get("id") or f"__idx_{idx}"
            tcs = m.get("tool_calls")
            tccs = m.get("tool_call_chunks")
            raw_content = m.get("content", "") if isinstance(m.get("content", ""), str) else ""
            # Only apply space restoration to the LAST (newest) AI message
            if idx == last_ai_idx:
                content = _restore_english_spaces(raw_content) if raw_content else ""
            else:
                content = raw_content
        else:
            msg_type = getattr(m, "type", "")
            if msg_type not in ("ai", "AIMessage", "AIMessageChunk"):
                continue
            msg_id = getattr(m, "id", None)
            if not isinstance(msg_id, str):
                msg_id = getattr(m, "lc_id", None) or f"__objid_{id(m)}"
            tcs = getattr(m, "tool_calls", None)
            tccs = getattr(m, "tool_call_chunks", None)
            content_attr = getattr(m, "content", "")
            raw_content = content_attr if isinstance(content_attr, str) else ""
            # Only apply space restoration to the LAST (newest) AI message
            if idx == last_ai_idx:
                content = _restore_english_spaces(raw_content) if raw_content else ""
            else:
                content = raw_content
        names: list[str] = []
        if isinstance(tcs, (list, tuple)):
            for tc in tcs:
                n = _tool_call_display_name(tc)
                if n:
                    names.append(n)
        if isinstance(tccs, (list, tuple)):
            for tcc in tccs:
                n = _tool_call_display_name(tcc)
                if n and n not in names:
                    names.append(n)
        if names:
            cache[str(msg_id)] = names
        new_content = _patch_one_message_content(m, names, content)
        # Only mutate if actually changed to avoid spurious downstream updates.
        if new_content != content:
            if isinstance(m, dict):
                m["content"] = new_content
            else:
                try:
                    m.content = new_content
                except Exception:
                    pass


def _enrich_tool_call_content_in_serialized(obj: Any, *, run_id: str = "") -> None:
    """After serialization, patch content from [工具调用已省略] → [工具调用: name1, name2].

    Works on the serialized ``[chunk_dict, metadata_dict]`` tuple produced
    by :func:`serialize` with ``mode="messages"``.
    """
    if not isinstance(obj, (list, tuple)) or len(obj) < 1:
        return
    chunk_dict = obj[0]
    if not isinstance(chunk_dict, dict):
        return
    content = chunk_dict.get("content", "")
    chunk_id = chunk_dict.get("id", "?")
    chunk_type = chunk_dict.get("type", "?")
    if not isinstance(content, str):
        return

    tool_calls = chunk_dict.get("tool_calls")
    tool_call_chunks = chunk_dict.get("tool_call_chunks")

    needs_patch = (
        content == ""
        or content == _TOOL_OMISSION_MARKER
        or "工具调用" in content
    )
    if not needs_patch:
        return

    names: list[str] = []
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            name = _tool_call_display_name(tc)
            if name and name not in names:
                names.append(name)
    if isinstance(tool_call_chunks, list):
        for tcc in tool_call_chunks:
            if not isinstance(tcc, dict):
                continue
            name = _tool_call_display_name(tcc)
            if name and name not in names:
                names.append(name)

    # Fallback: streamed AIMessageChunk has no tool_calls/tool_call_chunks,
    # but the values-stream may have already cached the full AI message's
    # tool_calls keyed by message id.
    # IMPORTANT: Only match by exact msg_id. Do NOT use fallback to last cache
    # entry, as it would incorrectly apply previous messages' tool names to
    # the current message.
    if not names and run_id:
        msg_id = chunk_dict.get("id")
        cache = _VALUES_AI_TOOL_CACHE.get(run_id) or {}
        if isinstance(msg_id, str) and msg_id in cache:
            names = list(cache[msg_id])

    if names:
        new_marker = "[工具调用: " + ", ".join(names) + "]"
        # Check if current content already has meaningful text (not just markers)
        current_content = chunk_dict.get("content", "")
        if not isinstance(current_content, str):
            current_content = ""
        # Remove existing tool call markers to check for actual text content
        content_without_markers = current_content
        for marker in ["[工具调用已省略]", "[内部消息]"]:
            content_without_markers = content_without_markers.replace(marker, "")
        # Also remove any existing [工具调用: ...] markers
        content_without_markers = re.sub(r"\[工具调用:[^\]]*\]", "", content_without_markers)
        has_text = bool(content_without_markers.strip())

        if has_text:
            # Preserve existing text, append new marker
            chunk_dict["content"] = current_content + "\n" + new_marker
        else:
            # Replace empty or marker-only content with named marker
            chunk_dict["content"] = new_marker


def _make_skill_content_redactor(
    app_config: AppConfig | None,
    runtime_context: Any | None = None,
) -> SkillContentRedactor:
    return SkillContentRedactor.from_run_context(
        app_config=app_config,
        runtime_context=runtime_context if isinstance(runtime_context, dict) else None,
        boundary="runtime",
    )


def _restrict_unsafe_tracing_callbacks(config: dict[str, Any]) -> None:
    """Keep only callbacks that explicitly promise Skill-safe payload handling.

    A Gateway run can load an enabled Skill at any point.  Provider callbacks
    receive raw prompts and ToolMessages before user-boundary redaction, so
    callbacks are fail-closed unless they opt into the safety contract.
    """

    callbacks = config.get("callbacks")
    if callbacks is None:
        return
    materialized = list(callbacks) if not isinstance(callbacks, list) else callbacks
    config["callbacks"] = [callback for callback in materialized if getattr(callback, "deerflow_skill_content_safe", False) is True]


def _log_cleanup_exception(task: asyncio.Task, run_id: str, logger: logging.Logger) -> None:
    """Log an exception raised by the bridge cleanup task."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Bridge cleanup failed for run %s",
            run_id,
            exc_info=exc,
        )


# Valid stream_mode values for LangGraph's graph.astream()
_VALID_LG_MODES = {"values", "updates", "checkpoints", "tasks", "debug", "messages", "custom"}


def _build_runtime_context(
    thread_id: str,
    run_id: str,
    caller_context: Any | None,
    app_config: AppConfig | None = None,
) -> dict[str, Any]:
    """Build the dict that becomes ``ToolRuntime.context`` for the run.

    Always includes ``thread_id`` and ``run_id``. Additional keys from the caller's
    ``config['context']`` (e.g. ``agent_name`` for the bootstrap flow — issue #2677)
    are merged in but never override ``thread_id``/``run_id``. The resolved
    ``AppConfig`` is added by the worker so tools can consume it without ambient
    global lookups.

    langgraph 1.1+ surfaces this as ``runtime.context`` via the parent runtime stored
    under ``config['configurable']['__pregel_runtime']`` — see
    ``langgraph.pregel.main`` where ``parent_runtime.merge(...)`` is invoked.
    """
    runtime_ctx: dict[str, Any] = {"thread_id": thread_id, "run_id": run_id}
    if isinstance(caller_context, dict):
        for key, value in caller_context.items():
            runtime_ctx.setdefault(key, value)
    if app_config is not None:
        runtime_ctx["app_config"] = app_config
    return runtime_ctx


@dataclass(frozen=True)
class RunContext:
    """Infrastructure dependencies for a single agent run.

    Groups checkpointer, store, and persistence-related singletons so that
    ``run_agent`` (and any future callers) receive one object instead of a
    growing list of keyword arguments.
    """

    checkpointer: Any
    store: Any | None = field(default=None)
    event_store: Any | None = field(default=None)
    run_events_config: Any | None = field(default=None)
    thread_store: Any | None = field(default=None)
    app_config: AppConfig | None = field(default=None)


def _install_runtime_context(config: dict, runtime_context: dict[str, Any]) -> None:
    existing_context = config.get("context")
    if isinstance(existing_context, dict):
        existing_context.setdefault("thread_id", runtime_context["thread_id"])
        existing_context.setdefault("run_id", runtime_context["run_id"])
        if "app_config" in runtime_context:
            existing_context["app_config"] = runtime_context["app_config"]
        return

    config["context"] = dict(runtime_context)


def _get_runtime_config(config: dict) -> dict[str, Any]:
    """Merge legacy configurable options with LangGraph runtime context."""
    cfg = dict(config.get("configurable", {}) or {})
    context = config.get("context", {}) or {}
    if isinstance(context, dict):
        cfg.update(context)
    return cfg


def _should_track_run_tokens(record: RunRecord, run_events_config: Any | None) -> bool:
    """Keep published-run accounting mandatory even when event tracking is off."""
    if bool((record.metadata or {}).get("published_agent")):
        return True
    return bool(getattr(run_events_config, "track_token_usage", True))


def _current_turn_has_attachment(graph_input: dict) -> bool:
    messages = graph_input.get("messages")
    if not isinstance(messages, list):
        return False

    for message in messages:
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if additional_kwargs is None and isinstance(message, dict):
            additional_kwargs = message.get("additional_kwargs")
        if isinstance(additional_kwargs, dict) and additional_kwargs.get("files"):
            return True

        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") not in (None, "text"):
                    return True
    return False


def _current_turn_has_tool_context(graph_input: dict) -> bool:
    messages = graph_input.get("messages")
    if not isinstance(messages, list):
        return False

    for message in messages:
        if getattr(message, "type", None) == "tool":
            return True
        if isinstance(message, dict) and message.get("type") == "tool":
            return True
        if getattr(message, "tool_calls", None) or getattr(message, "invalid_tool_calls", None):
            return True
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if additional_kwargs is None and isinstance(message, dict):
            additional_kwargs = message.get("additional_kwargs")
        if isinstance(additional_kwargs, dict) and additional_kwargs.get("tool_calls"):
            return True
    return False


def _thread_has_historical_uploads(thread_id: str) -> bool:
    try:
        from deerflow.config.paths import get_paths

        uploads_dir = get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())
        return uploads_dir.exists() and any(path.is_file() for path in uploads_dir.iterdir())
    except Exception:
        logger.debug("Failed to inspect uploads for flash fast path", exc_info=True)
        return True


def _should_use_flash_direct_path(
    *,
    graph_input: dict,
    config: dict,
    thread_id: str,
    interrupt_before: list[str] | Literal["*"] | None,
    interrupt_after: list[str] | Literal["*"] | None,
) -> bool:
    cfg = _get_runtime_config(config)
    if cfg.get("is_bootstrap"):
        return False
    if cfg.get("skill_name"):
        return False
    if cfg.get("connector_ids"):
        return False
    if cfg.get("external_allowed_skills") is not None:
        return False
    if interrupt_before or interrupt_after:
        return False
    if cfg.get("mode") != "flash":
        return False
    if cfg.get("is_plan_mode", False) or cfg.get("subagent_enabled", False):
        return False
    if _current_turn_has_attachment(graph_input):
        return False
    if _current_turn_has_tool_context(graph_input):
        return False
    if _thread_has_historical_uploads(thread_id):
        return False
    return True


def _message_has_tool_call_request(message: Any) -> bool:
    if getattr(message, "tool_calls", None):
        return True
    if getattr(message, "invalid_tool_calls", None):
        return True
    if getattr(message, "tool_call_chunks", None):
        return True
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("tool_calls"):
        return True
    return False


def _queue_flash_memory_capture(
    *,
    thread_id: str,
    messages: list[Any],
    app_config: AppConfig,
) -> None:
    """Queue a completed flash-direct conversation for the shared memory pipeline."""
    memory_config = getattr(app_config, "memory", None)
    if memory_config is None or not memory_config.enabled:
        return

    from deerflow.agents.memory.message_processing import filter_messages_for_memory
    from deerflow.agents.memory.queue import get_memory_queue

    filtered_messages = filter_messages_for_memory(messages)
    has_user = any(getattr(message, "type", None) == "human" for message in filtered_messages)
    has_assistant = any(getattr(message, "type", None) == "ai" for message in filtered_messages)
    if not has_user or not has_assistant:
        return

    get_memory_queue().add(
        thread_id=thread_id,
        messages=filtered_messages,
        user_id=get_effective_user_id(),
    )


def _compute_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return "app_config" in inspect.signature(agent_factory).parameters
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=128)
def _cached_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    return _compute_agent_factory_supports_app_config(agent_factory)


def _agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return _cached_agent_factory_supports_app_config(agent_factory)
    except TypeError:
        # Some callable instances are unhashable; fall back to a direct check.
        return _compute_agent_factory_supports_app_config(agent_factory)


def _normalize_lg_modes(requested_modes: set[str]) -> list[str]:
    lg_modes: list[str] = []
    for m in requested_modes:
        if m == "messages-tuple":
            lg_modes.append("messages")
        elif m == "events":
            continue
        elif m in _VALID_LG_MODES:
            lg_modes.append(m)
    if not lg_modes:
        lg_modes = ["values"]

    seen: set[str] = set()
    deduped: list[str] = []
    for m in lg_modes:
        if m not in seen:
            seen.add(m)
            deduped.append(m)
    return deduped


def _coerce_messages(raw_messages: Any) -> list[BaseMessage]:
    from langchain_core.messages import BaseMessage
    from langchain_core.messages.utils import convert_to_messages

    if not raw_messages:
        return []
    if not isinstance(raw_messages, list):
        raw_messages = [raw_messages]

    messages: list[BaseMessage] = []
    for message in raw_messages:
        if isinstance(message, BaseMessage):
            messages.append(message)
        else:
            try:
                converted = convert_to_messages([message])
            except (TypeError, ValueError, NotImplementedError):
                logger.debug("Skipping non-coercible message in flash direct path: %r", message, exc_info=True)
                continue
            messages.extend(converted)
    return messages


def _checkpoint_channel_values(ckpt_tuple: Any | None) -> dict[str, Any]:
    checkpoint = getattr(ckpt_tuple, "checkpoint", None) if ckpt_tuple is not None else None
    if not isinstance(checkpoint, dict):
        return {}
    values = checkpoint.get("channel_values")
    return dict(values) if isinstance(values, dict) else {}


def _checkpoint_channel_versions(ckpt_tuple: Any | None) -> dict[str, Any]:
    checkpoint = getattr(ckpt_tuple, "checkpoint", None) if ckpt_tuple is not None else None
    if not isinstance(checkpoint, dict):
        return {}
    versions = checkpoint.get("channel_versions")
    return dict(versions) if isinstance(versions, dict) else {}


def _extract_fallback_title(messages: list[Any]) -> str | None:
    """Return a short fallback title from the first genuine human message."""
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type != "human":
            continue

        # Skip dynamic-context reminders injected by DynamicContextMiddleware.
        # They carry an additional_kwargs flag; if the object is a plain dict,
        # fall back to a content-heuristic.
        additional_kwargs = getattr(msg, "additional_kwargs", None) or {}
        if isinstance(additional_kwargs, dict) and additional_kwargs.get("dynamic_context_reminder"):
            continue

        content = getattr(msg, "content", "") or ""
        if isinstance(content, list):
            # Extract text from multimodal content blocks
            texts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
            content = " ".join(texts)
        elif not isinstance(content, str):
            content = str(content)

        text = content.strip().replace("\n", " ")
        if not text:
            continue

        max_len = 50
        if len(text) > max_len:
            return text[:max_len].rstrip() + "..."
        return text

    return None


def _next_channel_version(checkpointer: Any, current: Any) -> Any:
    get_next_version = getattr(checkpointer, "get_next_version", None)
    if callable(get_next_version):
        return get_next_version(current, None)
    if isinstance(current, int):
        return current + 1
    return 1


def _flash_direct_checkpoint_metadata(ckpt_tuple: Any | None) -> dict[str, Any]:
    """Build LangGraph-compatible checkpoint metadata for flash-direct writes.

    LangGraph resumes from the latest checkpoint via
    ``checkpoint_metadata["step"] + 1`` (see ``AsyncPregelLoop.__aenter__``).
    Flash-direct bypasses the graph, so we must advance ``step`` ourselves or
    the next full-graph run (e.g. switching from flash to pro) raises
    ``KeyError('step')``.
    """
    previous_metadata = getattr(ckpt_tuple, "metadata", None) if ckpt_tuple is not None else None
    if not isinstance(previous_metadata, dict):
        previous_metadata = {}

    parents = previous_metadata.get("parents")
    if not isinstance(parents, dict):
        parents = {}

    return {
        "source": "flash_direct",
        "step": previous_metadata.get("step", -1) + 1,
        "parents": parents,
    }


async def _persist_flash_direct_checkpoint(
    *,
    checkpointer: Any | None,
    thread_id: str,
    ckpt_tuple: Any | None,
    channel_values: dict[str, Any],
    changed_channels: set[str],
) -> None:
    if checkpointer is None:
        return

    previous_checkpoint = getattr(ckpt_tuple, "checkpoint", None) if ckpt_tuple is not None else None
    previous_versions = _checkpoint_channel_versions(ckpt_tuple)
    checkpoint = empty_checkpoint()
    if isinstance(previous_checkpoint, dict):
        checkpoint["versions_seen"] = copy.deepcopy(previous_checkpoint.get("versions_seen", {}))
        checkpoint["pending_sends"] = copy.deepcopy(previous_checkpoint.get("pending_sends", []))

    new_versions: dict[str, Any] = {}
    channel_versions = dict(previous_versions)
    for channel in changed_channels:
        next_version = _next_channel_version(checkpointer, previous_versions.get(channel))
        channel_versions[channel] = next_version
        new_versions[channel] = next_version

    checkpoint["channel_values"] = channel_values
    checkpoint["channel_versions"] = channel_versions
    checkpoint["updated_channels"] = sorted(changed_channels)

    base_config = getattr(ckpt_tuple, "config", None) if ckpt_tuple is not None else None
    if not isinstance(base_config, dict):
        base_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    else:
        base_config = copy.deepcopy(base_config)
        base_config.setdefault("configurable", {})
        base_config["configurable"].setdefault("thread_id", thread_id)
        base_config["configurable"].setdefault("checkpoint_ns", "")

    await _call_checkpointer_method(
        checkpointer,
        "aput",
        "put",
        base_config,
        checkpoint,
        _flash_direct_checkpoint_metadata(ckpt_tuple),
        new_versions,
    )


async def _run_flash_direct_model(
    *,
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    ctx: RunContext,
    graph_input: dict,
    config: dict,
    runnable_config: Any,
    requested_modes: set[str],
    stream_subgraphs: bool,
    checkpointer: Any | None,
    pre_run_checkpoint_tuple: Any | None,
    redactor: SkillContentRedactor,
) -> bool:
    from langchain_core.messages import AIMessage, SystemMessage, message_chunk_to_message

    from deerflow.agents.lead_agent.agent import _resolve_available_skill_names, _resolve_model_name
    from deerflow.agents.lead_agent.prompt import apply_prompt_template
    from deerflow.config.agents_config import validate_agent_name
    from deerflow.config.app_config import get_app_config
    from deerflow.models.factory import get_cached_chat_model
    from deerflow.publishing.runtime_loader import (
        resolve_runtime_agent_config,
        resolve_runtime_agent_instructions,
    )

    cfg = _get_runtime_config(config)
    app_config = ctx.app_config or get_app_config()

    agent_name = validate_agent_name(cfg.get("agent_name"))
    agent_config = resolve_runtime_agent_config(cfg, agent_name=agent_name)
    agent_instructions = resolve_runtime_agent_instructions(cfg)
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")
    agent_model_name = agent_config.model if agent_config and agent_config.model else None
    model_name = _resolve_model_name(requested_model_name or agent_model_name, app_config=app_config)
    model_config = app_config.get_model_config(model_name)
    if model_config is None:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")
    if record.model_name is not None and model_name != record.model_name:
        await run_manager.update_model_name(record.run_id, model_name)

    existing_values = _checkpoint_channel_values(pre_run_checkpoint_tuple)
    historical_messages = _coerce_messages(existing_values.get("messages"))
    input_messages = _coerce_messages(graph_input.get("messages"))
    conversation_messages = [*historical_messages, *input_messages]

    cleaned_conversation = []
    for msg in conversation_messages:
        msg_type = getattr(msg, "type", "")
        # Only clean AI and system messages, not human messages
        if hasattr(msg, "content") and msg_type in ("ai", "system", "tool"):
            msg = _clean_aimessage_content(msg)
        cleaned_conversation.append(msg)
    conversation_messages = cleaned_conversation

    # --- Human-message multimodal normalization (see module-level helper).
    conversation_messages = [
        _normalize_human_multimodal_content_for_provider(msg)
        for msg in conversation_messages
    ]

    # Debug log: summarize every message that is about to be sent to the
    # provider so 400 "text content is empty" / multimodal issues can be
    # diagnosed without a debug traceback.
    _preview: list[str] = []
    for i, msg in enumerate(conversation_messages):
        mtype = getattr(msg, "type", "?")
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            parts_summary = ", ".join(
                f"{(p.get('type') if isinstance(p, dict) else type(p).__name__)}"
                for p in content[:6]
            )
            if len(content) > 6:
                parts_summary += f", …(+{len(content) - 6})"
            _preview.append(f"[{i}] {mtype}(list:{parts_summary})")
        else:
            snippet = (
                str(content).replace("\n", " ")[:120]
                if content is not None
                else ""
            )
            _preview.append(f"[{i}] {mtype}: {snippet}")
    logger.info(
        "[LLM_INPUT_PREVIEW] run=%s total=%s messages:\n%s",
        record.run_id,
        len(conversation_messages),
        "\n".join(_preview),
    )

    system_prompt = apply_prompt_template(
        subagent_enabled=False,
        max_concurrent_subagents=cfg.get("max_concurrent_subagents", 3),
        agent_name=agent_name,
        available_skills=_resolve_available_skill_names(
            agent_config,
            False,
            cfg.get("skill_name"),
            app_config=app_config,
            external_allowed_skills=cfg.get("external_allowed_skills"),
        ),
        app_config=app_config,
        agent_instructions=agent_instructions,
    )
    model_messages = [SystemMessage(content=system_prompt), *conversation_messages]

    model = get_cached_chat_model(
        name=model_name,
        thinking_enabled=False,
        reasoning_effort=cfg.get("reasoning_effort"),
        app_config=app_config,
    ).with_config(tags=["lead_agent"])

    lg_modes = _normalize_lg_modes(requested_modes)
    logger.info("Run %s: flash direct streaming with modes %s (requested: %s)", record.run_id, lg_modes, requested_modes)

    accumulated_chunk: Any | None = None
    streamed_chunks: list[Any] = []
    metadata = {"langgraph_node": "agent", "tags": ["lead_agent"], "flash_direct": True}
    async for chunk in model.astream(model_messages, config=runnable_config):
        if record.abort_event.is_set():
            logger.info("Run %s abort requested - stopping flash direct stream", record.run_id)
            break
        accumulated_chunk = chunk if accumulated_chunk is None else accumulated_chunk + chunk
        streamed_chunks.append(chunk)

    final_ai_message = message_chunk_to_message(accumulated_chunk) if accumulated_chunk is not None else AIMessage(content="")
    if not record.abort_event.is_set() and _message_has_tool_call_request(final_ai_message):
        logger.info("Run %s: flash direct model requested tool calls; falling back to full agent graph", record.run_id)
        return False

    if "messages" in lg_modes:
        for chunk in streamed_chunks:
            cleaned_chunk = _clean_aimessage_content(chunk)
            safe_chunk = redactor.redact_stream_payload(
                "messages",
                (cleaned_chunk, metadata),
                run_id=record.run_id,
            )
            serialized = serialize(safe_chunk, mode="messages")
            _enrich_tool_call_content_in_serialized(serialized, run_id=record.run_id)
            await bridge.publish(record.run_id, _lg_mode_to_sse_event("messages"), serialized)

    final_ai_message = _clean_aimessage_content(final_ai_message)
    final_messages = [*conversation_messages, final_ai_message]
    channel_values = {
        **existing_values,
        "messages": final_messages,
        "artifacts": existing_values.get("artifacts") or [],
    }

    if "values" in lg_modes:
        # Patch final AI message content so the banner shows tool names.
        _cache_and_patch_values_messages(record.run_id, final_messages)
        channel_values["messages"] = final_messages
        safe_values = redactor.redact_stream_payload("values", channel_values, run_id=record.run_id)
        await bridge.publish(record.run_id, "values", serialize(safe_values, mode="values"))

    if not record.abort_event.is_set():
        await _persist_flash_direct_checkpoint(
            checkpointer=checkpointer,
            thread_id=record.thread_id,
            ckpt_tuple=pre_run_checkpoint_tuple,
            channel_values=channel_values,
            changed_channels={"messages", "artifacts"},
        )
        _queue_flash_memory_capture(
            thread_id=record.thread_id,
            messages=final_messages,
            app_config=app_config,
        )

    if stream_subgraphs:
        logger.debug("Run %s: flash direct path ignores stream_subgraphs because no graph/subgraphs are created", record.run_id)
    _drop_values_cache(record.run_id)
    return True


# ---------------------------------------------------------------------------
# User-friendly LLM / runtime error mapping
# ---------------------------------------------------------------------------


def _collect_exception_text(exc: BaseException) -> str:
    """Build a lower-cased combined text from the whole exception chain.

    Providers often nest SDK wrapper exceptions (httpx, OpenAI, LiteLLM)
    around the raw provider response, so matching only against
    ``str(exc)`` misses the real signal (e.g. ``403 PermissionError`` on
    the ``__cause__`` instead of the top-level ``APIStatusError``).
    """
    parts: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        try:
            parts.append(str(cur))
        except Exception:
            pass
        try:
            # Many SDKs expose a structured body with .message/.code/.type
            body = getattr(cur, "body", None)
            if isinstance(body, dict):
                for key in ("message", "error", "code", "type", "detail", "msg"):
                    v = body.get(key)
                    if isinstance(v, str) and v:
                        parts.append(v)
                    elif isinstance(v, dict):
                        for nested_key in ("message", "type", "code"):
                            nv = v.get(nested_key)
                            if isinstance(nv, str) and nv:
                                parts.append(nv)
        except Exception:
            pass
        # Also inspect args[0] when it's a dict (some Kimi/OpenAI wrappers
        # store the parsed JSON there).
        args = getattr(cur, "args", None)
        if isinstance(args, tuple) and args:
            first = args[0]
            if isinstance(first, str):
                parts.append(first)
            elif isinstance(first, dict):
                for key in ("message", "code", "type", "error", "detail"):
                    v = first.get(key)
                    if isinstance(v, str) and v:
                        parts.append(v)
        cur = cur.__cause__ or cur.__context__
    return " ".join(parts).lower()


def _friendly_error_message(exc: BaseException) -> str:
    """Translate common provider exceptions into human-readable Chinese text.

    The raw error returned from providers (e.g. Kimi, OpenAI-compatible APIs)
    often leaks provider jargon, Python dict reprs, or HTTP details to the
    end user. This helper maps well-known patterns into friendly messages.
    """
    raw_text = _collect_exception_text(exc)
    # Log the raw signal once per failure so admins can extend this mapping
    # without having to reproduce a full traceback — the friendly text is
    # intentionally non-technical.
    logger.info(
        "[LLM_ERROR_MATCH] type=%s raw_snippet=%s",
        type(exc).__name__,
        raw_text[:500] if raw_text else "",
    )
    # 1) Concurrent / rate limits (Kimi: "concurrent request limit", generic: "too many requests")
    if "concurrent request limit" in raw_text or "access_terminated_error" in raw_text:
        return "当前同时运行的请求太多了，请稍等几秒，等之前的请求处理完后再试试。"
    if (
        "rate limit" in raw_text
        or "too many requests" in raw_text
        or "429" in raw_text
        or "403" in raw_text
    ):
        # Most shared-key setups surface concurrency limits as 403 too; match
        # after the precise concurrent-request check above.
        return "接口调用频率过高或并发数超限，请稍等一会儿再试。"
    # 2) Auth / quota
    if (
        "authentication" in raw_text
        or "invalid api key" in raw_text
        or "401" in raw_text
        or "unauthorized" in raw_text
        or "permission denied" in raw_text
    ):
        return "模型认证失败，请联系管理员检查模型配置。"
    if (
        "quota" in raw_text
        or "insufficient balance" in raw_text
        or "no balance" in raw_text
        or "余额不足" in raw_text
        or "out of credit" in raw_text
    ):
        return "模型账户的配额或余额不足，请联系管理员充值或调整配额。"
    # 3) Content / safety
    if (
        "content policy" in raw_text
        or "safety" in raw_text
        or "rejected" in raw_text
        or "内容安全" in raw_text
        or "sensitive" in raw_text
    ):
        return "请求内容不符合模型的安全策略，已被拒绝。"
    # 4) Context / tokens
    if (
        "context length" in raw_text
        or "maximum context" in raw_text
        or "max tokens" in raw_text
        or "prompt is too long" in raw_text
        or "token limit" in raw_text
        or "max new tokens" in raw_text
    ):
        return "对话内容太长，请先清理一下对话历史或缩短输入内容后再试。"
    # 5) Model not configured / not found / 5xx server-side
    if (
        "model not found" in raw_text
        or "no such model" in raw_text
        or "invalid model" in raw_text
        or "model is not" in raw_text
    ):
        return "当前配置的模型不可用，请联系管理员检查模型设置。"
    if (
        "502" in raw_text
        or "503" in raw_text
        or "504" in raw_text
        or "bad gateway" in raw_text
        or "service unavailable" in raw_text
        or "gateway timeout" in raw_text
        or "upstream error" in raw_text
        or "internal server error" in raw_text
        or "500" in raw_text
    ):
        return "模型服务暂时不可用，请稍后再试或联系管理员。"
    # 6) Network / timeout
    if "timeout" in raw_text or "timed out" in raw_text:
        return "模型响应超时，请稍后再试。"
    if (
        ("connection" in raw_text and ("error" in raw_text or "refused" in raw_text or "reset" in raw_text))
        or "econnrefused" in raw_text
        or "name or service not known" in raw_text
        or "dns" in raw_text
    ):
        return "暂时无法连接到模型服务，请稍后再试。"
    # 7) 400 / invalid request — most commonly empty text when the user
    #    sends a pure picture/attachment with no accompanying prompt. Kimi
    #    surfaces this as 'text content is empty' + type: invalid_request_error.
    #    Intent is intentionally NARROW: if we can't tell for sure that "empty
    #    text" is the reason, DON'T pretend it is — let the generic fallback
    #    and details banner tell the user/administrator what really happened.
    import re as _re_400
    empty_text_re = _re_400.compile(
        r"(text|message|prompt|content)\s*(content|message|body)?\s*(is|cannot|can not|can't|be)?\s*(empty|blank|missing)",
        _re_400.IGNORECASE,
    )
    _empty_hit = False
    if "text content is empty" in raw_text:
        _empty_hit = True
    elif empty_text_re.search(raw_text):
        _empty_hit = True
    elif (
        "invalid_request_error" in raw_text and empty_text_re.search(raw_text)
    ):
        _empty_hit = True
    elif "400" in raw_text and empty_text_re.search(raw_text):
        _empty_hit = True
    elif "request must contain either prompt or image_url" in raw_text:
        _empty_hit = True
    if _empty_hit:
        return "模型未接收到有效文字内容。若您只发送了图片或文件，系统已自动添加分析提示；请重试，或补充具体描述以获得更准确的结果。"
    # Generic fallback — keep it short and Chinese, no technical jargon.
    return "模型处理请求时发生了错误，请稍后再试。"


# Delimiter + HTML-comment wrapper used to preserve the raw provider-error
# snippet inside the final AI message content when we patch it with a
# human-friendly translation.  The frontend ``friendlyAiErrorMessage`` knows
# how to peel off this comment so the user sees only the clean sentence,
# while the "原始错误详情" expander still has the real text to display.
_RAW_ERROR_SNIPPET_PREFIX = "\n<!--DF_RAW_ERROR:"
_RAW_ERROR_SNIPPET_SUFFIX = "-->"
_RAW_ERROR_SNIPPET_PREFIX_STRIP = "<!--DF_RAW_ERROR:"
_RAW_ERROR_RE = None  # compiled lazily in the helpers below


def _wrap_friendly_error(friendly_text: str, raw_snippet: str) -> str:
    """Append a hidden raw-error HTML comment onto a friendly sentence.

    The comment is deliberately placed on a new line so end users looking
    at a plain-text dump of the checkpoint still see the clean Chinese
    message on line 1 first.  The snippet is clipped to 2000 chars to
    avoid bloating the ``messages`` channel with huge HTTP response bodies.
    """
    if not raw_snippet:
        return friendly_text
    clipped = raw_snippet.strip()
    if len(clipped) > 2000:
        clipped = clipped[:2000] + "\n…(truncated)"
    # Escape any literal "-->" inside the snippet so we can't accidentally
    # close the HTML comment before we intend to (defense in depth).
    clipped_safe = clipped.replace("-->", "--\\>").replace("\0", "")
    return (
        str(friendly_text).rstrip()
        + _RAW_ERROR_SNIPPET_PREFIX
        + clipped_safe
        + _RAW_ERROR_SNIPPET_SUFFIX
    )


def _replace_last_ai_message_in_checkpoint(
    checkpointer: Any, thread_id: str, friendly_text: str
) -> None:
    """Best-effort patch the last AI message content in the checkpoint.

    When an LLM call fails mid-stream, raw error text sometimes leaks into
    the checkpoint's ``messages`` channel as the content of the last AI
    message.  This helper tries to overwrite it with a friendly Chinese
    sentence so the thread history stays readable.
    """
    if checkpointer is None or not thread_id:
        return
    try:
        import asyncio as _asyncio

        loop = _asyncio.get_event_loop()
        if loop.is_running():
            # Called from inside an async context; defer to caller's task
            return
    except Exception:
        return


async def _apatch_last_ai_message_in_checkpoint(
    checkpointer: Any, thread_id: str, friendly_text: str
) -> None:
    """Async counterpart of :func:`_replace_last_ai_message_in_checkpoint`."""
    if checkpointer is None or not thread_id:
        return
    try:
        cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        ckpt_tuple = await checkpointer.aget_tuple(cfg)
        if ckpt_tuple is None:
            return
        checkpoint = getattr(ckpt_tuple, "checkpoint", None)
        if not isinstance(checkpoint, dict):
            return
        channel_values = checkpoint.get("channel_values")
        if not isinstance(channel_values, dict):
            return
        messages = channel_values.get("messages")
        if not isinstance(messages, list) or not messages:
            return
        # Walk backwards and patch the first AI/system message whose content
        # still looks like an error (HTTP codes, dict reprs of provider errors).
        error_signals = ("error code", "concurrent request", "LLM request failed",
                         "rate limit", "{'error':", "\"error\":", "access_terminated")
        for m in reversed(messages):
            mtype = getattr(m, "type", "") or (m.get("type") if isinstance(m, dict) else "")
            if mtype not in ("ai", "AIMessage", "AIMessageChunk"):
                continue
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None)
            if not isinstance(content, str):
                continue
            low = content.lower()
            if any(sig in low for sig in error_signals):
                try:
                    if isinstance(m, dict):
                        m["content"] = friendly_text
                    else:
                        m.content = friendly_text  # type: ignore[union-attr]
                except Exception:
                    pass
                break
    except Exception:
        # Checkpoint mutation is best-effort; never raise from here.
        logger.debug("Failed to patch last AI message in checkpoint for %s", thread_id, exc_info=True)


async def _areplace_last_ai_message_content_in_checkpoint(
    checkpointer: Any, thread_id: str, new_content: str
) -> None:
    """Replace the content of the last AI message in checkpoint with new_content.

    Used for post-processing like English space restoration where we need to
    update the persisted message content after streaming completes.
    """
    if checkpointer is None or not thread_id:
        return
    try:
        cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        ckpt_tuple = await checkpointer.aget_tuple(cfg)
        if ckpt_tuple is None:
            return
        checkpoint = getattr(ckpt_tuple, "checkpoint", None)
        if not isinstance(checkpoint, dict):
            return
        channel_values = checkpoint.get("channel_values")
        if not isinstance(channel_values, dict):
            return
        messages = channel_values.get("messages")
        if not isinstance(messages, list) or not messages:
            return
        # Walk backwards and replace the first AI message's content
        for m in reversed(messages):
            mtype = getattr(m, "type", "") or (m.get("type") if isinstance(m, dict) else "")
            if mtype not in ("ai", "AIMessage", "AIMessageChunk"):
                continue
            try:
                if isinstance(m, dict):
                    m["content"] = new_content
                else:
                    m.content = new_content
                logger.info("Replaced last AI message content in checkpoint for %s (len=%d)", thread_id, len(new_content))
            except Exception:
                pass
            break
        # Persist the updated checkpoint
        await checkpointer.aput(cfg, checkpoint)
    except Exception:
        logger.debug("Failed to replace last AI message content in checkpoint for %s", thread_id, exc_info=True)


async def run_agent(
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    *,
    ctx: RunContext,
    agent_factory: Any,
    graph_input: dict,
    config: dict,
    stream_modes: list[str] | None = None,
    stream_subgraphs: bool = False,
    interrupt_before: list[str] | Literal["*"] | None = None,
    interrupt_after: list[str] | Literal["*"] | None = None,
) -> None:
    """Execute an agent in the background, publishing events to *bridge*."""

    # Unpack infrastructure dependencies from RunContext.
    checkpointer = ctx.checkpointer
    store = ctx.store
    event_store = ctx.event_store
    run_events_config = ctx.run_events_config
    thread_store = ctx.thread_store

    run_id = record.run_id
    thread_id = record.thread_id
    requested_modes: set[str] = set(stream_modes or ["values"])
    pre_run_checkpoint_id: str | None = None
    pre_run_snapshot: dict[str, Any] | None = None
    pre_run_checkpoint_tuple: Any | None = None
    snapshot_capture_failed = False
    redactor = _make_skill_content_redactor(ctx.app_config, config.get("context"))

    journal = None

    # Track whether "events" was requested but skipped
    if "events" in requested_modes:
        logger.info(
            "Run %s: 'events' stream_mode not supported in gateway (requires astream_events + checkpoint callbacks). Skipping.",
            run_id,
        )

    try:
        # Initialize RunJournal + write human_message event.
        # These are inside the try block so any exception (e.g. a DB
        # error writing the event) flows through the except/finally
        # path that publishes an "end" event to the SSE bridge —
        # otherwise a failure here would leave the stream hanging
        # with no terminator.
        if event_store is not None:
            from deerflow.runtime.journal import RunJournal

            journal = RunJournal(
                run_id=run_id,
                thread_id=thread_id,
                event_store=event_store,
                track_token_usage=_should_track_run_tokens(record, run_events_config),
                progress_reporter=lambda snapshot: run_manager.update_run_progress(run_id, **snapshot),
                redactor=redactor,
            )

        # 1. Mark running
        await run_manager.set_status(run_id, RunStatus.running)

        # Snapshot the latest pre-run checkpoint so rollback can restore it.
        if checkpointer is not None:
            try:
                config_for_check = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(config_for_check)
                pre_run_checkpoint_tuple = ckpt_tuple
                if ckpt_tuple is not None:
                    ckpt_config = getattr(ckpt_tuple, "config", {}).get("configurable", {})
                    pre_run_checkpoint_id = ckpt_config.get("checkpoint_id")
                    pre_run_snapshot = {
                        "checkpoint_ns": ckpt_config.get("checkpoint_ns", ""),
                        "checkpoint": copy.deepcopy(getattr(ckpt_tuple, "checkpoint", {})),
                        "metadata": copy.deepcopy(getattr(ckpt_tuple, "metadata", {})),
                        "pending_writes": copy.deepcopy(getattr(ckpt_tuple, "pending_writes", []) or []),
                    }

                    # Clean corrupted messages in checkpoint to prevent 400 errors
                    channel_values = getattr(ckpt_tuple, "checkpoint", {}).get("channel_values", {})
                    messages = channel_values.get("messages") if isinstance(channel_values, dict) else None
                    if messages and isinstance(messages, list):
                        needs_clean = False
                        cleaned_messages = []
                        for m in messages:
                            msg_type = getattr(m, "type", "")
                            # Only clean AI and system messages, not human messages
                            if hasattr(m, "content") and msg_type in ("ai", "system", "tool"):
                                original_content = m.content
                                is_empty_after_clean = False
                                if isinstance(original_content, str):
                                    # Extract tool names from raw text before cleaning
                                    ext_names = _extract_tool_names_from_text(original_content)
                                    cleaned_text = _clean_model_text(original_content)
                                    needs_patch = (
                                        cleaned_text != original_content
                                        or (not cleaned_text and original_content.strip())
                                        or original_content.strip() in ("[工具调用已省略]", "")
                                        or "工具调用" in original_content
                                    )
                                    if needs_patch:
                                        tc_names = list(ext_names)
                                        if hasattr(m, "tool_calls") and m.tool_calls:
                                            for tc in m.tool_calls:
                                                name = _tool_call_display_name(tc)
                                                if name and name not in tc_names:
                                                    tc_names.append(name)
                                        if hasattr(m, "tool_call_chunks") and m.tool_call_chunks:
                                            for tcc in m.tool_call_chunks:
                                                name = _tool_call_display_name(tcc)
                                                if name and name not in tc_names:
                                                    tc_names.append(name)
                                        if tc_names:
                                            m.content = cleaned_text or ("[工具调用: " + ", ".join(tc_names) + "]")
                                        else:
                                            m.content = cleaned_text or "[工具调用已省略]"
                                        needs_clean = True
                                        is_empty_after_clean = not cleaned_text
                                if hasattr(m, "additional_kwargs") and isinstance(m.additional_kwargs, dict):
                                    rc = m.additional_kwargs.get("reasoning_content")
                                    if isinstance(rc, str):
                                        cleaned_rc = _clean_model_text(rc)
                                        if cleaned_rc != rc:
                                            m.additional_kwargs["reasoning_content"] = cleaned_rc
                                            needs_clean = True
                                if is_empty_after_clean and hasattr(m, "tool_calls") and m.tool_calls:
                                    pass
                                elif is_empty_after_clean:
                                    needs_clean = True
                            cleaned_messages.append(m)

                        if needs_clean:
                            channel_values["messages"] = cleaned_messages
                            checkpoint_data = getattr(ckpt_tuple, "checkpoint", {})
                            checkpoint_data["channel_values"] = channel_values
                            metadata = getattr(ckpt_tuple, "metadata", {})
                            try:
                                await checkpointer.aput(
                                    config_for_check,
                                    checkpoint_data,
                                    metadata,
                                    getattr(ckpt_tuple, "pending_writes", {}) or {},
                                )
                            except Exception:
                                logger.warning("Failed to write cleaned checkpoint for run %s", run_id, exc_info=True)
            except Exception:
                snapshot_capture_failed = True
                logger.warning("Could not capture pre-run checkpoint snapshot for run %s", run_id, exc_info=True)

        # 2. Publish metadata — useStream needs both run_id AND thread_id
        await bridge.publish(
            run_id,
            "metadata",
            {
                "run_id": run_id,
                "thread_id": thread_id,
            },
        )

        # 3. Build the agent
        from langchain_core.runnables import RunnableConfig
        from langgraph.runtime import Runtime

        # Inject runtime context so middlewares and tools (via ToolRuntime.context) can
        # access thread-level data. langgraph-cli does this automatically; we must do it
        # manually here because we drive the graph through ``agent.astream(config=...)``
        # without passing the official ``context=`` parameter.
        runtime_ctx = _build_runtime_context(thread_id, run_id, config.get("context"), ctx.app_config)
        # Expose the run-scoped journal under a sentinel key so middleware can
        # write audit events (e.g. SafetyFinishReasonMiddleware recording
        # suppressed tool calls). Double-underscore prefix marks it as a
        # runtime-internal channel; user code must not depend on the key name.
        if journal is not None:
            runtime_ctx["__run_journal"] = journal
        _install_runtime_context(config, runtime_ctx)
        from deerflow.publishing.runtime_loader import hydrate_runtime_agent_config

        await hydrate_runtime_agent_config(
            config,
            owner_user_id=str(runtime_ctx.get("user_id") or get_effective_user_id()),
        )
        runtime = Runtime(context=cast(Any, runtime_ctx), store=store)
        config.setdefault("configurable", {})["__pregel_runtime"] = runtime

        # Inject RunJournal as a LangChain callback handler.
        # on_llm_end captures token usage; on_chain_start/end captures lifecycle.
        if journal is not None:
            config.setdefault("callbacks", []).append(journal)

        # Inject Langfuse trace-attribute metadata so the langchain CallbackHandler
        # can lift session_id / user_id / trace_name / tags onto the root trace.
        # Shared helper with ``DeerFlowClient.stream`` so both entry points stay
        # in sync; caller-provided metadata wins via setdefault inside the helper.
        inject_langfuse_metadata(
            config,
            thread_id=thread_id,
            user_id=get_effective_user_id(),
            assistant_id=record.assistant_id,
            model_name=record.model_name,
            environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
        )

        # Resolve after runtime context installation so context/configurable reflect
        # the agent name that this run will actually execute.
        config.setdefault("run_name", resolve_root_run_name(config, record.assistant_id))
        config.setdefault("configurable", {})["__agent_graph_runtime_key"] = (
            id(checkpointer) if checkpointer is not None else None,
            id(store) if store is not None else None,
            tuple(interrupt_before or ()),
            tuple(interrupt_after or ()),
        )
        runnable_config = RunnableConfig(**config)
        _restrict_unsafe_tracing_callbacks(runnable_config)
        if _should_use_flash_direct_path(
            graph_input=graph_input,
            config=config,
            thread_id=thread_id,
            interrupt_before=interrupt_before,
            interrupt_after=interrupt_after,
        ):
            flash_direct_handled = await _run_flash_direct_model(
                bridge=bridge,
                run_manager=run_manager,
                record=record,
                ctx=ctx,
                graph_input=graph_input,
                config=config,
                runnable_config=runnable_config,
                requested_modes=requested_modes,
                stream_subgraphs=stream_subgraphs,
                checkpointer=checkpointer,
                pre_run_checkpoint_tuple=pre_run_checkpoint_tuple,
                redactor=redactor,
            )
            if flash_direct_handled:
                if record.abort_event.is_set():
                    action = record.abort_action
                    if action == "rollback":
                        await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
                        try:
                            await _rollback_to_pre_run_checkpoint(
                                checkpointer=checkpointer,
                                thread_id=thread_id,
                                run_id=run_id,
                                pre_run_checkpoint_id=pre_run_checkpoint_id,
                                pre_run_snapshot=pre_run_snapshot,
                                snapshot_capture_failed=snapshot_capture_failed,
                            )
                            logger.info("Run %s rolled back to pre-run checkpoint %s", run_id, pre_run_checkpoint_id)
                        except Exception:
                            logger.warning("Failed to rollback checkpoint for run %s", run_id, exc_info=True)
                    else:
                        await run_manager.set_status(run_id, RunStatus.interrupted)
                else:
                    await run_manager.set_status(run_id, RunStatus.success)
                return

        if ctx.app_config is not None and _agent_factory_supports_app_config(agent_factory):
            agent = agent_factory(config=runnable_config, app_config=ctx.app_config)
        else:
            agent = agent_factory(config=runnable_config)

        # Agent factories attach tracing callbacks at graph construction time.
        # Remove any callback that cannot guarantee pre-export Skill redaction.
        _restrict_unsafe_tracing_callbacks(runnable_config)

        # Capture the effective (resolved) model name from the agent's metadata.
        # _resolve_model_name in agent.py may return the default model if the
        # requested name is not in the allowlist — this update ensures the
        # persisted model_name reflects the actual model used.
        if record.model_name is not None:
            resolved = getattr(agent, "metadata", {}) or {}
            if isinstance(resolved, dict):
                effective = resolved.get("model_name")
                if effective and effective != record.model_name:
                    await run_manager.update_model_name(record.run_id, effective)

        # 4. Attach checkpointer and store
        if checkpointer is not None:
            agent.checkpointer = checkpointer
        if store is not None:
            agent.store = store

        # 5. Set interrupt nodes
        if interrupt_before:
            agent.interrupt_before_nodes = interrupt_before
        if interrupt_after:
            agent.interrupt_after_nodes = interrupt_after

        # 6. Build LangGraph stream_mode list
        #    "events" is NOT a valid astream mode — skip it
        #    "messages-tuple" maps to LangGraph's "messages" mode
        lg_modes: list[str] = []
        for m in requested_modes:
            if m == "messages-tuple":
                lg_modes.append("messages")
            elif m == "events":
                # Skipped — see log above
                continue
            elif m in _VALID_LG_MODES:
                lg_modes.append(m)
        if not lg_modes:
            lg_modes = ["values"]

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for m in lg_modes:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        lg_modes = deduped

        logger.info("Run %s: streaming with modes %s (requested: %s)", run_id, lg_modes, requested_modes)

        # Clean any markers from input messages before passing to agent
        cleaned_graph_input = dict(graph_input) if isinstance(graph_input, dict) else graph_input
        if isinstance(cleaned_graph_input, dict) and "messages" in cleaned_graph_input:
            msgs = cleaned_graph_input["messages"]
            if isinstance(msgs, list):
                cleaned_msgs = []
                for m in msgs:
                    msg_type = getattr(m, "type", "")
                    # Only clean AI and system messages, not human messages
                    if hasattr(m, "content") and msg_type in ("ai", "system", "tool"):
                        m = _clean_aimessage_content(m)
                        if hasattr(m, "content"):
                            c = m.content
                            is_empty_or_marker = (
                                not c.strip()
                                or c.strip() == "[工具调用已省略]"
                                or "工具调用" in c
                            ) if isinstance(c, str) else False
                            if isinstance(c, str) and is_empty_or_marker:
                                # Try to extract from content text first
                                tc_names = _extract_tool_names_from_text(c)
                                if hasattr(m, "tool_calls") and m.tool_calls:
                                    for tc in m.tool_calls:
                                        name = _tool_call_display_name(tc)
                                        if name and name not in tc_names:
                                            tc_names.append(name)
                                if hasattr(m, "tool_call_chunks") and m.tool_call_chunks:
                                    for tcc in m.tool_call_chunks:
                                        name = _tool_call_display_name(tcc)
                                        if name and name not in tc_names:
                                            tc_names.append(name)
                                if tc_names:
                                    m.content = "[工具调用: " + ", ".join(tc_names) + "]"
                    cleaned_msgs.append(m)
                cleaned_graph_input["messages"] = cleaned_msgs

                # Inject whitespace preservation rule when the last user message
                # is written primarily in English / space-delimited Latin
                # scripts.  This overrides any token-saving bias the model may
                # have picked up for multilingual turns, without affecting
                # Chinese / CJK turns.
                _inject_english_whitespace_rule_if_needed(cleaned_graph_input)

                graph_input = cleaned_graph_input

        # 7. Stream using graph.astream
        if len(lg_modes) == 1 and not stream_subgraphs:
            # Single mode, no subgraphs: astream yields raw chunks
            single_mode = lg_modes[0]
            async for chunk in agent.astream(graph_input, config=runnable_config, stream_mode=single_mode):
                if record.abort_event.is_set():
                    logger.info("Run %s abort requested — stopping", run_id)
                    break
                # Skip space restoration in streaming (chunks too small for correct restoration)
                cleaned_chunk = _clean_aimessage_content(chunk, skip_space_restoration=(single_mode == "messages")) if single_mode == "messages" else chunk
                sse_event = _lg_mode_to_sse_event(single_mode)
                if single_mode == "values" and isinstance(cleaned_chunk, dict):
                    # Pre-process values: ensure every AI message in state has
                    # a [工具调用: name1, name2] marker before publishing.
                    # This also restores English spaces on complete messages.
                    msgs = cleaned_chunk.get("messages")
                    if isinstance(msgs, list):
                        _cache_and_patch_values_messages(run_id, msgs)
                        cleaned_chunk["messages"] = msgs
                safe_chunk = redactor.redact_stream_payload(single_mode, cleaned_chunk, run_id=run_id)
                serialized_chunk = serialize(safe_chunk, mode=single_mode)
                if single_mode == "messages":
                    _enrich_tool_call_content_in_serialized(serialized_chunk, run_id=run_id)
                elif single_mode == "values":
                    if isinstance(serialized_chunk, dict):
                        msgs = serialized_chunk.get("messages")
                        if isinstance(msgs, list):
                            _cache_ai_tool_names_from_values(run_id, msgs)
                await bridge.publish(run_id, sse_event, serialized_chunk)
        else:
            # Multiple modes or subgraphs: astream yields tuples
            item_count = 0
            async for item in agent.astream(
                graph_input,
                config=runnable_config,
                stream_mode=lg_modes,
                subgraphs=stream_subgraphs,
            ):
                if record.abort_event.is_set():
                    logger.info("Run %s abort requested — stopping", run_id)
                    break

                mode, chunk = _unpack_stream_item(item, lg_modes, stream_subgraphs)
                item_count += 1
                if mode is None:
                    continue

                # Skip space restoration in streaming (chunks too small for correct restoration)
                cleaned_chunk = _clean_aimessage_content(chunk, is_streaming_chunk=(mode == "messages"), skip_space_restoration=(mode == "messages")) if mode == "messages" else chunk
                sse_event = _lg_mode_to_sse_event(mode)
                if mode == "values":
                    if isinstance(cleaned_chunk, dict):
                        msgs = cleaned_chunk.get("messages")
                        if isinstance(msgs, list):
                            _cache_and_patch_values_messages(run_id, msgs)
                            cleaned_chunk["messages"] = msgs
                safe_chunk = redactor.redact_stream_payload(mode, cleaned_chunk, run_id=run_id)
                serialized_chunk = serialize(safe_chunk, mode=mode)
                if mode == "messages":
                    _enrich_tool_call_content_in_serialized(serialized_chunk, run_id=run_id)
                elif mode == "values":
                    if isinstance(serialized_chunk, dict):
                        msgs = serialized_chunk.get("messages")
                        if isinstance(msgs, list):
                            _cache_ai_tool_names_from_values(run_id, msgs)
                await bridge.publish(run_id, sse_event, serialized_chunk)

        # 7b. Final-content patch for glued-English output.
        # LangGraph "messages" mode publishes individual AIMessageChunk values
        # while the model is still producing tokens — each chunk is too short
        # for reliable English space restoration and we skip restoration there.
        # Once the whole graph run is done, the final state lives in the
        # checkpointer (and is mirrored by what values-mode would have emitted).
        # If the consumer did NOT request "values" mode, the displayed message
        # text never gets a second pass at the restored spacing.  Emit a final
        # values event here so every consumer (including the sandbox chat page)
        # re-renders the newest AI message with spaces properly inserted.
        # We only do this on the normal/success path (abort handling is below).
        if not record.abort_event.is_set() and checkpointer is not None:
            try:
                from langgraph.checkpoint.base import CheckpointTuple
                ckpt: CheckpointTuple | None = await checkpointer.aget_tuple(config_for_check)
                if ckpt is not None:
                    cv = getattr(ckpt.checkpoint, "channel_values", None)
                    if isinstance(cv, dict):
                        msgs_copy = None
                        for key_name in ("messages",):
                            msgs = cv.get(key_name)
                            if isinstance(msgs, list):
                                # Operate on a shallow dict copy so the cached
                                # checkpoint object is not mutated in place.
                                if msgs_copy is None:
                                    cv = dict(cv)
                                    msgs_copy = list(msgs)
                                    cv[key_name] = msgs_copy
                                # Apply the same patching pipeline as values-mode
                                _cache_and_patch_values_messages(run_id, msgs_copy)
                        if msgs_copy is not None:
                            # Redact, serialize, and emit a values event.
                            safe = redactor.redact_stream_payload(
                                "values", cv, run_id=run_id,
                            )
                            serialized_vals = serialize(safe, mode="values")
                            # Also refresh the tool-name cache from this view.
                            if isinstance(serialized_vals, dict):
                                vm = serialized_vals.get("messages")
                                if isinstance(vm, list):
                                    _cache_ai_tool_names_from_values(run_id, vm)
                            await bridge.publish(run_id, "values", serialized_vals)
                            logger.info(
                                "Run %s: emitted final values patch event for glued-English content (msgs=%d)",
                                run_id,
                                len(msgs_copy) if isinstance(msgs_copy, list) else 0,
                            )
            except Exception:
                logger.debug(
                    "Run %s: final values patch skipped (non-fatal)",
                    run_id,
                    exc_info=True,
                )

        # 8. Final status
        if record.abort_event.is_set():
            action = record.abort_action
            if action == "rollback":
                await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
                try:
                    await _rollback_to_pre_run_checkpoint(
                        checkpointer=checkpointer,
                        thread_id=thread_id,
                        run_id=run_id,
                        pre_run_checkpoint_id=pre_run_checkpoint_id,
                        pre_run_snapshot=pre_run_snapshot,
                        snapshot_capture_failed=snapshot_capture_failed,
                    )
                    logger.info("Run %s rolled back to pre-run checkpoint %s", run_id, pre_run_checkpoint_id)
                except Exception:
                    logger.warning("Failed to rollback checkpoint for run %s", run_id, exc_info=True)
            else:
                await run_manager.set_status(run_id, RunStatus.interrupted)
        else:
            await run_manager.set_status(run_id, RunStatus.success)

    except asyncio.CancelledError:
        action = record.abort_action
        if action == "rollback":
            await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
            try:
                await _rollback_to_pre_run_checkpoint(
                    checkpointer=checkpointer,
                    thread_id=thread_id,
                    run_id=run_id,
                    pre_run_checkpoint_id=pre_run_checkpoint_id,
                    pre_run_snapshot=pre_run_snapshot,
                    snapshot_capture_failed=snapshot_capture_failed,
                )
                logger.info("Run %s was cancelled and rolled back", run_id)
            except Exception:
                logger.warning("Run %s cancellation rollback failed", run_id, exc_info=True)
        else:
            await run_manager.set_status(run_id, RunStatus.interrupted)
            logger.info("Run %s was cancelled", run_id)

    except Exception as exc:
        from deerflow.agents.middlewares.token_usage_middleware import (
            PUBLISHED_RUN_TOKEN_BUDGET_ERROR,
            PublishedRunTokenLimitError,
        )

        if isinstance(exc, PublishedRunTokenLimitError):
            error_msg = PUBLISHED_RUN_TOKEN_BUDGET_ERROR
            raw_snippet = ""
        else:
            error_msg = _friendly_error_message(exc)
            raw_snippet = _collect_exception_text(exc)
        # Preserve the raw snippet as a stripped HTML comment inside the
        # checkpoint patch, so the frontend can render it in the '查看原始
        # 错误详情' expander.  `run_manager.set_status` / bridge.error take
        # the clean friendly message only.
        patched_checkpoint_text = _wrap_friendly_error(error_msg, raw_snippet)
        logger.error(
            "Run %s failed with %s (friendly=%s, raw_snippet_len=%s)",
            run_id,
            type(exc).__name__,
            error_msg,
            len(raw_snippet or ""),
            exc_info=True,
        )
        # Overwrite the raw provider error in the checkpoint so the thread
        # history doesn't leak ugly HTTP / dict reprs to end users.
        await _apatch_last_ai_message_in_checkpoint(
            checkpointer, thread_id, patched_checkpoint_text
        )
        await run_manager.set_status(run_id, RunStatus.error, error=error_msg)
        await bridge.publish(
            run_id,
            "error",
            {
                "message": error_msg,
                "name": type(exc).__name__,
                "raw": (raw_snippet[:1500] if raw_snippet else None),
            },
        )

    finally:
        # Drop cross-stream values cache to avoid unbounded memory use.
        _drop_values_cache(run_id)
        # Flush any buffered journal events and persist completion data
        if journal is not None:
            try:
                await journal.flush()
            except Exception:
                logger.warning("Failed to flush journal for run %s", run_id, exc_info=True)

            try:
                # Persist token usage + convenience fields to RunStore
                completion = journal.get_completion_data()
                await run_manager.update_run_completion(run_id, status=record.status.value, **completion)
            except Exception:
                logger.warning("Failed to persist run completion for %s (non-fatal)", run_id, exc_info=True)

        # Sync title from checkpoint to threads_meta.display_name.
        # For paths that bypass the agent graph (e.g. flash direct), fall back
        # to a local title derived from the first human message.
        if checkpointer is not None and thread_store is not None:
            try:
                ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(ckpt_config)
                if ckpt_tuple is not None:
                    ckpt = getattr(ckpt_tuple, "checkpoint", {}) or {}
                    channel_values = dict(ckpt.get("channel_values") or {})
                    title = channel_values.get("title")

                    if not title:
                        fallback = _extract_fallback_title(channel_values.get("messages", []))
                        if fallback:
                            title = fallback
                            channel_values["title"] = title
                            await _persist_flash_direct_checkpoint(
                                checkpointer=checkpointer,
                                thread_id=thread_id,
                                ckpt_tuple=ckpt_tuple,
                                channel_values=channel_values,
                                changed_channels={"title"},
                            )

                    if title:
                        await thread_store.update_display_name(thread_id, title)
            except Exception:
                logger.debug("Failed to sync title for thread %s (non-fatal)", thread_id, exc_info=True)

        # Update threads_meta status based on run outcome
        if thread_store is not None:
            try:
                final_status = "idle" if record.status == RunStatus.success else record.status.value
                await thread_store.update_status(thread_id, final_status)
            except Exception:
                logger.debug("Failed to update thread_meta status for %s (non-fatal)", thread_id)

        await bridge.publish_end(run_id)

        cleanup_task = asyncio.create_task(bridge.cleanup(run_id, delay=60))
        cleanup_task.add_done_callback(lambda task: _log_cleanup_exception(task, run_id, logger))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call_checkpointer_method(checkpointer: Any, async_name: str, sync_name: str, *args: Any, **kwargs: Any) -> Any:
    """Call a checkpointer method, supporting async and sync variants."""
    method = getattr(checkpointer, async_name, None) or getattr(checkpointer, sync_name, None)
    if method is None:
        raise AttributeError(f"Missing checkpointer method: {async_name}/{sync_name}")
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _rollback_to_pre_run_checkpoint(
    *,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    pre_run_checkpoint_id: str | None,
    pre_run_snapshot: dict[str, Any] | None,
    snapshot_capture_failed: bool,
) -> None:
    """Restore thread state to the checkpoint snapshot captured before run start."""
    if checkpointer is None:
        logger.info("Run %s rollback requested but no checkpointer is configured", run_id)
        return

    if snapshot_capture_failed:
        logger.warning("Run %s rollback skipped: pre-run checkpoint snapshot capture failed", run_id)
        return

    if pre_run_snapshot is None:
        await _call_checkpointer_method(checkpointer, "adelete_thread", "delete_thread", thread_id)
        logger.info("Run %s rollback reset thread %s to empty state", run_id, thread_id)
        return

    checkpoint_to_restore = None
    metadata_to_restore: dict[str, Any] = {}
    checkpoint_ns = ""
    checkpoint = pre_run_snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        logger.warning("Run %s rollback skipped: invalid pre-run checkpoint snapshot", run_id)
        return
    checkpoint_to_restore = checkpoint
    if checkpoint_to_restore.get("id") is None and pre_run_checkpoint_id is not None:
        checkpoint_to_restore = {**checkpoint_to_restore, "id": pre_run_checkpoint_id}
    if checkpoint_to_restore.get("id") is None:
        logger.warning("Run %s rollback skipped: pre-run checkpoint has no checkpoint id", run_id)
        return
    restore_marker = _new_checkpoint_marker()
    checkpoint_to_restore = {
        **checkpoint_to_restore,
        "id": restore_marker["id"],
        "ts": restore_marker["ts"],
    }
    metadata = pre_run_snapshot.get("metadata", {})
    metadata_to_restore = metadata if isinstance(metadata, dict) else {}
    raw_checkpoint_ns = pre_run_snapshot.get("checkpoint_ns")
    checkpoint_ns = raw_checkpoint_ns if isinstance(raw_checkpoint_ns, str) else ""

    channel_versions = checkpoint_to_restore.get("channel_versions")
    new_versions = dict(channel_versions) if isinstance(channel_versions, dict) else {}

    restore_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    restored_config = await _call_checkpointer_method(
        checkpointer,
        "aput",
        "put",
        restore_config,
        checkpoint_to_restore,
        metadata_to_restore if isinstance(metadata_to_restore, dict) else {},
        new_versions,
    )
    if not isinstance(restored_config, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config: expected dict")
    restored_configurable = restored_config.get("configurable", {})
    if not isinstance(restored_configurable, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config payload")
    restored_checkpoint_id = restored_configurable.get("checkpoint_id")
    if not restored_checkpoint_id:
        raise RuntimeError(f"Run {run_id} rollback restore did not return checkpoint_id")

    pending_writes = pre_run_snapshot.get("pending_writes", [])
    if not pending_writes:
        return

    writes_by_task: dict[str, list[tuple[str, Any]]] = {}
    for item in pending_writes:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write is not a 3-tuple: {item!r}")
        task_id, channel, value = item
        if not isinstance(channel, str):
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write has non-string channel: task_id={task_id!r}, channel={channel!r}")
        writes_by_task.setdefault(str(task_id), []).append((channel, value))

    for task_id, writes in writes_by_task.items():
        await _call_checkpointer_method(
            checkpointer,
            "aput_writes",
            "put_writes",
            restored_config,
            writes,
            task_id=task_id,
        )


def _new_checkpoint_marker() -> dict[str, str]:
    marker = empty_checkpoint()
    return {"id": marker["id"], "ts": marker["ts"]}


def _lg_mode_to_sse_event(mode: str) -> str:
    """Map LangGraph internal stream_mode name to SSE event name.

    LangGraph's ``astream(stream_mode="messages")`` produces message
    tuples.  The SSE protocol calls this ``messages-tuple`` when the
    client explicitly requests it, but the default SSE event name used
    by LangGraph Platform is simply ``"messages"``.
    """
    # All LG modes map 1:1 to SSE event names — "messages" stays "messages"
    return mode


def _extract_human_message(graph_input: dict) -> HumanMessage | None:
    """Extract or construct a HumanMessage from graph_input for event recording.

    Returns a LangChain HumanMessage so callers can use .model_dump() to get
    the checkpoint-aligned serialization format.
    """
    from langchain_core.messages import HumanMessage

    messages = graph_input.get("messages")
    if not messages:
        return None
    last = messages[-1] if isinstance(messages, list) else messages
    if isinstance(last, HumanMessage):
        return last
    if isinstance(last, str):
        return HumanMessage(content=last) if last else None
    if hasattr(last, "content"):
        content = last.content
        return HumanMessage(content=content)
    if isinstance(last, dict):
        content = last.get("content", "")
        return HumanMessage(content=content) if content else None
    return None


def _unpack_stream_item(
    item: Any,
    lg_modes: list[str],
    stream_subgraphs: bool,
) -> tuple[str | None, Any]:
    """Unpack a multi-mode or subgraph stream item into (mode, chunk).

    Returns ``(None, None)`` if the item cannot be parsed.
    """
    if stream_subgraphs:
        if isinstance(item, tuple) and len(item) == 3:
            _ns, mode, chunk = item
            return str(mode), chunk
        if isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
            return str(mode), chunk
        return None, None

    if isinstance(item, tuple) and len(item) == 2:
        mode, chunk = item
        return str(mode), chunk

    # Fallback: single-element output from first mode
    return lg_modes[0] if lg_modes else None, item
