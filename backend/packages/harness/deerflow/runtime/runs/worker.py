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
    concatenated output (e.g. "Itseemsthemessage" instead of "It seems the
    message"). This is a last-line-of-defence fix for system prompts that
    accidentally instruct the model to eliminate whitespace in multilingual
    output.

    Two complementary strategies are applied inside each "plain" (non-fenced,
    non-URL, non-inline-code) chunk:

    1. CamelCase / TitleCase boundaries — lower→upper letter splits.  These are
       extremely reliable and create 0 false negatives, but cannot help with
       all-lowercase glued runs like ``awarmwelcomein``.
    2. Dictionary-based longest-match DP split using a compact built-in list
       of ~1200 of the most common English words.  This handles the
       all-lowercase runs without needing an external dependency.
    """
    if not text:
        return text

    # Fast-path: there must be at least 5 consecutive Latin letters somewhere
    # to be worth processing.  A purely numeric / CJK / very short input can
    # just go through untouched.
    if not re.search(r"[A-Za-z]{5,}", text):
        return text

    # Split the text into protected segments (code fences, inline code,
    # markdown links, urls) so we never touch their contents.  Everything
    # outside is "plain" and subject to restoration.
    segments: list[tuple[bool, str]] = []
    pattern = re.compile(
        r"(```[\s\S]*?```)"  # fenced code block
        r"|(`[^`\n]+`)"  # inline code
        r"|(https?://\S+)"  # URL
        r"|(\[[^\]]+\]\([^)]+\))",  # markdown link [text](url)
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

    _WORD_PREFIXES = (
        "un", "dis", "mis", "re", "pre", "post", "non", "anti", "over", "under",
        "out", "sub", "super", "inter", "trans", "auto", "bi", "co", "de", "ex",
        "extra", "hyper", "in", "im", "il", "ir", "infra", "intra", "macro",
        "mega", "meta", "micro", "mid", "mini", "mono", "multi", "neo", "non",
        "out", "over", "peri", "poly", "post", "pre", "pro", "proto", "pseudo",
        "quasi", "semi", "sub", "super", "supra", "tele", "thermo", "trans",
        "tri", "ultra", "uni", "vice",
    )

    def _looks_like_prefix(window: str) -> bool:
        """Return True when a CamelCase split should NOT be made.

        ``window`` ends at the character we are considering (an uppercase
        letter).  We inspect the very tail of ``window`` to see if the last
        few lowercase chars + final uppercase spell a word like "iPhone",
        "DeSantis", "eBay" or "McDonald" — the recognised pattern is a short
        English-derivational prefix (in-, re-, un-, pre-, …) immediately
        followed by a capitalised base word, or one of the handful of
        brand/proper-noun patterns.

        The old buggy implementation checked ``window.startswith(p)`` which
        caused false positives for any window that *began* with a prefix
        letter-combination (e.g. ``comeinE`` was rejected because it starts
        with ``co``).  We now test only the tail.
        """
        if len(window) < 2:
            return False
        low = window.lower()
        # last char is the uppercase letter we'd split on
        tail_upper = low[-1]
        # Proper-noun exceptions (Mc-, Mac-, O-, N-, etc.)
        for fixed in ("mc", "mac", "o'"):
            if len(low) > len(fixed) and low.endswith(fixed + tail_upper):
                return True
        # Derivational prefixes: 2-4 lowercase letters + [capital start of
        # the base word].  We only match prefixes of length exactly equal to
        # the candidate so "comeinE" (prefix_candidate = "in", last 2 lower
        # chars before final letter) is correctly identified as the in-E
        # boundary while "comeinE" doesn't accidentally match co- because
        # the co- is 13 chars before the split point.
        for p in _WORD_PREFIXES:
            L = len(p)
            if len(low) - 1 == L and low[:L] == p:
                # window exactly matches prefix + capital-letter
                return True
            if len(low) - 1 > L:
                last_segment = low[-(L + 1):]
                if last_segment.startswith(p) and last_segment.endswith(tail_upper):
                    return True
        return False

    def _split_plain(plain: str) -> str:
        n = len(plain)
        if n < 3:
            return plain

        # ---------------------------------------------------------------
        # Step 0: split the plain chunk into LATIN-RUNS (letters + ') vs
        # everything-else (digits, punctuation, markdown punctuation,
        # existing whitespace).  Non-Latin substrings are emitted
        # verbatim with their original characters; only the LATIN-RUNS
        # get the CamelCase + dictionary treatment.
        #
        # We never introduce a space between a punctuation character and
        # the adjacent word unless it was already there.  This avoids the
        # "Sure ! Here's" / "! :" type of regressions.
        # ---------------------------------------------------------------
        out_parts: list[str] = []
        run_start = 0
        i = 0
        while i < n:
            c = plain[i]
            is_latin = (("A" <= c <= "Z") or ("a" <= c <= "z") or c == "'")
            run_is_latin = (("A" <= plain[run_start] <= "Z") or
                            ("a" <= plain[run_start] <= "z") or
                            plain[run_start] == "'")
            if is_latin != run_is_latin:
                sub = plain[run_start:i]
                if run_is_latin:
                    out_parts.append(_split_one_latin_run(sub))
                else:
                    out_parts.append(sub)
                run_start = i
            i += 1
        # Flush tail
        sub = plain[run_start:]
        tail_is_latin = bool(sub) and (
            ("A" <= sub[0] <= "Z") or ("a" <= sub[0] <= "z") or sub[0] == "'"
        )
        if tail_is_latin:
            out_parts.append(_split_one_latin_run(sub))
        else:
            out_parts.append(sub)
        return "".join(out_parts)

    def _split_one_latin_run(frag: str) -> str:
        """Apply CamelCase flags + dict-DP to a single contiguous run of
        Latin letters/apostrophes.  Never called with punctuation/whitespace.
        """
        n = len(frag)
        if n < 3:
            return frag
        flags = [False] * n  # True -> insert space BEFORE char i

        # ---------- Pass 1: CamelCase / TitleCase boundaries ----------
        # Split every lower→upper letter boundary.  Derivational prefixes
        # (iPhone, DeSantis, eBay) will split into e.g. "i Phone", which is
        # an acceptable cosmetic tradeoff — the *vast* majority of
        # lowercase→uppercase transitions in glued model output are
        # legitimate word boundaries (HereAre, UserTable, QueryResults).
        i = 1
        while i < n:
            ch = frag[i]
            prev = frag[i - 1]
            if ch.isupper() and prev.isalpha() and prev.islower():
                flags[i] = True
                i += 1
                continue
            i += 1
        # Single-letter uppercase starters ("IDon'tKnow" → "I Don't Know")
        for i in range(1, min(n, 6)):
            if frag[i].isupper() and frag[i - 1].isupper() and i + 1 < n and frag[i + 1].islower():
                head = frag[:i]
                if len(head) == 1 and head in ("I", "A"):
                    flags[i] = True
                    break

        # ---------- Pass 2: dict-DP on each CamelCase subfragment ----------
        boundaries: list[int] = [0]
        for i in range(1, n):
            if flags[i]:
                boundaries.append(i)
        boundaries.append(n)

        rebuilt: list[str] = []
        for k in range(len(boundaries) - 1):
            b_start = boundaries[k]
            b_end = boundaries[k + 1]
            rebuilt.append(_dict_split_latin_run(frag[b_start:b_end]))

        spaced = " ".join(r for r in rebuilt if r)
        return re.sub(r"  +", " ", spaced)

    result_parts: list[str] = []
    for is_protected, chunk in segments:
        if is_protected:
            result_parts.append(chunk)
        else:
            result_parts.append(_split_plain(chunk))
    return "".join(result_parts)


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
through thus time times to today together told too took top toward town trade
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
dominant door double doubt draft dragon drama draw drink drive drop drug dry due
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
"""

# Build sets: full lowercase set for longest-match; also a title-case set so
# fragments like "QueryResults" that survive Pass-1 still get a second chance.
_EN_WORD_SET: set[str] = {w.lower() for w in _COMMON_EN_WORDS_RAW.split() if w.strip()}
# Maximum word length to search; used to cap inner DP lookahead.
_MAX_EN_WORD_LEN = max((len(w) for w in _EN_WORD_SET), default=16)

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


def _dict_split_latin_run(frag: str) -> str:
    """Split a single Latin run into space-separated dictionary words.

    Strategy: DP that maximises a combined score where:
      * A dictionary match of length ``L`` contributes ``L * L + L*2`` points
        (≈ favour longer, legitimate words).
      * If the ENTIRE fragment is itself a dictionary word (length >= 5) it
        receives a "full match" guard score so "standpoint" beats
        "stand point", "strategy" beats "st rate gy", etc.
      * A matched contraction ("here's") contributes the same as its parent
        word plus a tiny bonus.
      * Any **unmatched** (OOV) single character is carried forward with a
        small per-char bonus (base 1 + +2 per subsequent char in the run)
        so OOV acronyms/names stay glued instead of being shredded into
        2-letter junk words.

    Fragments that already have spaces or are shorter than 5 letters are
    returned untouched.
    """
    if not frag:
        return frag
    has_alpha = any(("A" <= c <= "Z") or ("a" <= c <= "z") for c in frag)
    if not has_alpha or len(frag) < 5 or " " in frag:
        return frag

    low = frag.lower()
    n = len(frag)
    # Fragment-level flag used by Tier-2 function-word gating.  When the
    # whole fragment is already a real dictionary word we tighten the gating
    # so 2-letter function words inside ("is", "it", "in", "an") do NOT
    # cause internal shredding.
    frag_is_full_word: bool = (n >= 5) and (low in _EN_WORD_SET)

    # ---------- DP that also tracks the kind of transition that landed us
    # here, so we can penalise "fragmented output": jumping back and forth
    # between dictionary words and OOV runs.
    #
    # State shape: dp[i][k] = best score arriving at position i, where k is
    #   0 -> the last action was a DICTIONARY / contraction split,
    #   1 -> the last action was an OOV carry-forward (or i == 0).
    # Each cell stores (score, prev_pos, prev_kind, len_of_prev_run).
    INF_NEG = -(1 << 30)
    # Switch penalty: crossing between a dict-split boundary and an OOV run
    # costs this many points.  This strongly favours "keep OOV together" over
    # the opportunistic "pick a 2-letter dict word, carry one char OOV, pick
    # another 2-letter dict word…" style of shredding.
    _SWITCH_PENALTY = 6
    _RUN_BONUS_BASE = 1    # first char of an OOV run
    _RUN_BONUS_CONT = 2    # each subsequent char of the same OOV run
    dp: list[list[tuple[int, int, int, int]]] = [
        [(INF_NEG, -1, -1, 0), (INF_NEG, -1, -1, 0)] for _ in range(n + 1)
    ]
    dp[0][1] = (0, -1, -1, 0)  # begin as if "previous" is OOV

    for i in range(n):
        ch = low[i]
        for last_kind in (0, 1):
            base_score, _, _, prev_run_len = dp[i][last_kind]
            if base_score <= INF_NEG:
                continue

            if not (("a" <= ch <= "z") or ch == "'"):
                # punctuation — just forward, inheriting last_kind unchanged
                ns = base_score
                if ns > dp[i + 1][last_kind][0]:
                    dp[i + 1][last_kind] = (ns, i, last_kind, prev_run_len + 1)
                continue

            # 1) OOV carry forward for this char.
            switch_cost = 0 if (last_kind == 1) else _SWITCH_PENALTY
            # Run-length booster: every subsequent char in the same OOV run
            # gets +2 so a 6-char OOV still peaks around 1 + 2*5 = 11 pts
            # and can't beat a legitimate 3-word dict split like "i+can+do"
            # (3+15+8 = 26 pts).
            run_bonus = (
                _RUN_BONUS_CONT
                if (last_kind == 1 and prev_run_len >= 1)
                else _RUN_BONUS_BASE
            )
            ns_oov = base_score + run_bonus - switch_cost
            if ns_oov > dp[i + 1][1][0]:
                new_run_len = prev_run_len + 1 if last_kind == 1 else 1
                dp[i + 1][1] = (ns_oov, i, last_kind, new_run_len)

            # 2) Dictionary / contraction matches starting at i.
            end_limit = min(n, i + _MAX_EN_WORD_LEN)
            j = i + 1
            while j <= end_limit:
                nxt = low[j - 1]
                if not (("a" <= nxt <= "z") or nxt == "'"):
                    break
                candidate = low[i:j]
                word_len = j - i
                score_add = 0
                if candidate in _EN_WORD_SET:
                    score_add = word_len * word_len + 2 * word_len
                    # Two-tier prefix guard (see module-level frozensets):
                    if word_len <= 3:
                        next_char_is_latin = (j < n) and (
                            ("a" <= low[j] <= "z") or (low[j] == "'")
                        )
                        if next_char_is_latin:
                            if candidate in _DICT_SPLIT_AMBIGUOUS_PREFIXES:
                                # Tier 1: always block mid-run.
                                score_add = 0
                            elif (candidate in _DICT_SPLIT_FUNCTION_WORDS_TIER2
                                  and frag_is_full_word):
                                # Tier 2: block mid-run ONLY when the whole
                                # fragment is already a real dictionary word.
                                score_add = 0
                elif "'" in candidate:
                    tick = candidate.index("'")
                    head = candidate[:tick]
                    tail = candidate[tick + 1:]
                    if head in _EN_WORD_SET and tail in _CONTRACTIONS:
                        score_add = (tick * tick + 2 * tick) + 4
                if score_add:
                    # Switch cost between OOV-kind and dict-kind.  Special
                    # waiver: when i==0 and the starting "OOV-kind" run is
                    # empty (which is the standard initial state), we have
                    # not actually consumed any OOV content, so charging 6
                    # points just to start the fragment with a dictionary
                    # word is unfair (it caused "Icando" to be kept as an
                    # OOV run because 20-6 < 21 for 6 chars of pure OOV).
                    if i == 0 and last_kind == 1 and prev_run_len == 0:
                        switch = 0
                    else:
                        switch = 0 if (last_kind == 0) else _SWITCH_PENALTY
                    new_total = base_score + score_add - switch
                    if new_total > dp[j][0][0]:
                        dp[j][0] = (new_total, i, last_kind, word_len)
                j += 1

    # 3) Full-word bonus: if the whole fragment is itself a dictionary word
    # of length >= 5, compare against a synthetic "keep as one token" score
    # so legitimate compound words (standpoint, strategy, independent, …)
    # defeat whatever internal split scores the DP found.  Using >= makes
    # ties resolve in favour of not-splitting (safer for real words).
    if frag_is_full_word:
        full_word_score = n * n + 2 * n
        if full_word_score >= max(dp[n][0][0], dp[n][1][0]):
            return frag

    # Pick whichever kind gives the best final score, then reconstruct by
    # walking back through prev_pos / prev_kind pointers.
    final_score_0, _, _, _ = dp[n][0]
    final_score_1, _, _, _ = dp[n][1]
    if max(final_score_0, final_score_1) <= INF_NEG:
        return frag
    best_kind = 0 if final_score_0 >= final_score_1 else 1

    pieces: list[str] = []
    pos = n
    kind = best_kind
    # Track where the current OOV run ends, so we can emit a single chunk
    # instead of one token per character (they all have prev_pos == pos - 1).
    oov_run_end = -1
    while pos > 0:
        score, prev_pos, prev_kind, _run_len = dp[pos][kind]
        if score <= INF_NEG or prev_pos < 0:
            break
        if kind == 1:
            # OOV cell: just walk back, remember the rightmost end of this
            # run (the one with the largest pos that's still OOV).
            if oov_run_end == -1:
                oov_run_end = pos
            pos = prev_pos
            kind = prev_kind
            # If we've left the OOV kind (or hit the start), flush the run.
            if kind != 1 or pos == 0:
                start = pos
                end = oov_run_end
                if end - start > 0:
                    pieces.append(frag[start:end])
                oov_run_end = -1
        else:
            # Dictionary / contraction cell — prev_pos is the real start of
            # this word, so the slice is a complete token.
            span = frag[prev_pos:pos]
            if span:
                pieces.append(span)
            kind = prev_kind
            pos = prev_pos
    # Edge: if we ended at pos==0 with an un-flushed OOV run starting at 0.
    if oov_run_end > 0 and pos == 0:
        pieces.append(frag[0:oov_run_end])
    pieces.reverse()
    return " ".join(p for p in pieces if p)


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


def _clean_model_text(text: str) -> str:
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
    text = _restore_english_spaces(text)
    text = text.strip()
    return text


def _clean_aimessage_content(obj: Any, is_streaming_chunk: bool = False) -> Any:
    """Recursively strip raw tool-call markers from AIMessage/AIMessageChunk content.
    
    Args:
        obj: The message object to clean
        is_streaming_chunk: If True, this is a streaming AIMessageChunk. 
            In streaming mode, do NOT add [工具调用已省略] markers to empty chunks,
            as these markers will be accumulated into the final AI message content.
            Markers should only be added to the final complete AIMessage in values mode.
    """
    if obj is None:
        return None
    if isinstance(obj, str):
        return _clean_model_text(obj)
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if k == "content" and isinstance(v, str):
                cleaned[k] = _clean_model_text(v)
            elif k == "content" and isinstance(v, list):
                cleaned[k] = [
                    _clean_model_text(item) if isinstance(item, str) else item
                    for item in v
                ]
            else:
                cleaned[k] = _clean_aimessage_content(v, is_streaming_chunk=is_streaming_chunk)
        return cleaned
    if isinstance(obj, (list, tuple)):
        return [_clean_aimessage_content(item, is_streaming_chunk=is_streaming_chunk) for item in obj]
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
                obj.content = _clean_model_text(content)
            elif isinstance(content, list):
                obj.content = [
                    _clean_model_text(item) if isinstance(item, str) else item
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
            elif not cleaned_content.strip():
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
    """
    if not isinstance(messages, list):
        return
    cache = _VALUES_AI_TOOL_CACHE.setdefault(run_id, {})
    for idx, m in enumerate(messages):
        if isinstance(m, dict):
            msg_type = m.get("type", "")
            if msg_type not in ("ai", "AIMessage", "AIMessageChunk"):
                continue
            msg_id = m.get("id") or f"__idx_{idx}"
            tcs = m.get("tool_calls")
            tccs = m.get("tool_call_chunks")
            content = m.get("content", "") if isinstance(m.get("content", ""), str) else ""
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
            content = content_attr if isinstance(content_attr, str) else ""
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
                cleaned_chunk = _clean_aimessage_content(chunk) if single_mode == "messages" else chunk
                sse_event = _lg_mode_to_sse_event(single_mode)
                if single_mode == "values" and isinstance(cleaned_chunk, dict):
                    # Pre-process values: ensure every AI message in state has
                    # a [工具调用: name1, name2] marker before publishing.
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

                cleaned_chunk = _clean_aimessage_content(chunk, is_streaming_chunk=(mode == "messages")) if mode == "messages" else chunk
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
        else:
            error_msg = "Run failed while processing the request."
        logger.error("Run %s failed with %s", run_id, type(exc).__name__, exc_info=True)
        await run_manager.set_status(run_id, RunStatus.error, error=error_msg)
        await bridge.publish(
            run_id,
            "error",
            {
                "message": error_msg,
                "name": type(exc).__name__,
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
