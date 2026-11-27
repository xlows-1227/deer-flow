"""Deterministic, offline content rules for skill uploads.

The LLM review in :mod:`deerflow.skills.security_scanner` is advisory only:
it can be unavailable (no quota, timeout, provider error) and it can be
fooled.  These rules are the part that always runs, costs nothing, and is
auditable.

Severity policy:

* ``block`` — structural or high-confidence dangerous patterns.  A skill
  containing one of these installs as ``failed`` and stays hidden from the
  model until its owner or an admin explicitly overrides the verdict.
* ``warn`` — heuristic / semantic signals.  They are surfaced to the uploader
  and recorded in the audit trail, but they never block.

Rule snippets are never echoed in ``message``: only a short hash of the match
is reported, plus the line number, so logs and API responses cannot leak
skill content.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

Severity = Literal["block", "warn"]

MAX_FINDINGS_PER_FILE = 50

# Domains that are routinely referenced by legitimate skill documentation.
# Matching is suffix-based (``api.github.com`` matches ``github.com``).
# Hosts that never carry request payloads: XML/JSON namespace identifiers,
# specification sites and loopback.  Business API hosts are deliberately absent
# so their traffic falls through to the (non-blocking) unknown-host warning.
_ALLOWLISTED_HOST_SUFFIXES = (
    "localhost",
    "127.0.0.1",
    "example.com",
    "w3.org",
    "json-schema.org",
    "schema.org",
    "apache.org",
    "mozilla.org",
    "creativecommons.org",
    "python.org",
    "nodejs.org",
    "pypi.org",
    "npmjs.com",
    "docker.com",
    "kubernetes.io",
    # OOXML namespace identifiers emitted by python-docx / openpyxl / python-pptx.
    # They are XML namespace names, never fetched over the network.
    "schemas.openxmlformats.org",
    "schemas.microsoft.com",
)

_NEGATION_MARKERS = (
    "never",
    "do not",
    "don't",
    "must not",
    "avoid",
    "禁止",
    "严禁",
    "不要",
    "不得",
    "切勿",
    "勿",
)

_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff\u2060]")

# Only shapes that actually carry a request body count as "sending".  A bare
# ``urllib.request.urlopen`` / ``http.client`` is usually a read-only GET, and a
# ``Referer`` header is not an egress channel.
_NETWORK_SEND_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:requests|httpx)\.(?:post|put|patch)\s*\("
    r"|\baxios\.(?:post|put)\s*\("
    r"|\bfetch\s*\("
    r"|\bInvoke-RestMethod\b"
    r"|\brequests\.request\s*\(\s*['\"](?:POST|PUT|PATCH)"
    r"|urllib\.request\.(?:urlopen|Request|build_opener)\s*\([^)]*data\s*="
    r"|\bcurl\b[^\n]{0,160}(?:-d|--data|-F|--form|--upload-file|-T)\b"
    r"|\bwget\b[^\n]{0,160}--post-(?:data|file)\b"
    r")"
)

# Archives uploaded to an unknown host are treated as bulk data exfiltration.
# Only archive *creation* counts: ``import zipfile`` when reading a .pptx is not.
_ARCHIVE_BUILD_RE = re.compile(
    r"(?ix)(?:"
    r"zipfile\.ZipFile\s*\([^)]*[\"'][wa]"
    r"|tarfile\.open\s*\([^)]*[\"'][wa]"
    r"|shutil\.make_archive\s*\("
    r"|\bzip\s+-[a-z]*r[a-z]*\s"
    r"|\btar\s+(?:-[a-z]*c[a-z]*f|c[a-z]*f)\b"
    r")"
)

# Local secret material: credential stores, browser data and key files.
_SENSITIVE_FILE_RE = re.compile(
    r"(?i)(?:~/\.ssh|\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.aws/config|\.netrc|\.npmrc|\.pypirc|"
    r"\.kube/config|\.docker/config\.json|/etc/shadow|Cookies\.sqlite|Login Data|Keychain|[\"'/]\.env\b)"
)

# A variable holding an environment value, later placed into a request body.
_ENV_ASSIGN_RE = re.compile(
    r"(?:const|let|var\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*=\s*[^\n]*"
    r"(?:os\.environ(?:\.get)?\s*[\[(]|os\.getenv\s*\(|process\.env(?:\.|\[))",
    re.IGNORECASE,
)
_ENV_DUMP_RE = re.compile(r"(?i)\b(?:data|json|files|body)\s*=\s*(?:dict\()?\s*(?:os\.environ|process\.env)")

_EMBEDDED_SECRET_MESSAGE = "Content appears to embed a live credential or private key."

# Generic ``name = literal`` secret assignments: ``App Secret：<32 chars>``,
# ``client_secret: "…"``, ``api_key=…``.  Provider-prefixed tokens (``sk-``,
# ``AKIA``, ``ghp_``, ``xox``) are handled by the ``embedded_secret`` rule
# patterns; this catches plain values.  ``(?<![/?&])`` keeps URL query
# parameters (``?access_token=…``) out, and the value is validated by
# :func:`_looks_like_literal_secret` so placeholders, environment lookups and
# template braces never match.
_SECRET_ASSIGN_RE = re.compile(
    r"(?ix)"
    r"(?<![/?&\w.-])"
    r"(?P<name>app[\s_-]?secret|client[\s_-]?secret|secret[\s_-]?key|api[\s_-]?key"
    r"|access[\s_-]?token|refresh[\s_-]?token|bot[\s_-]?token|webhook[\s_-]?secret"
    r"|signing[\s_-]?key|encryption[\s_-]?key|private[\s_-]?key)"
    r"[\"']?\s*[:=：]\s*[\"']?"
    r"(?P<value>[A-Za-z0-9_+][A-Za-z0-9_+\-]{15,})"
    r"(?![A-Za-z0-9_+\-])"
)

# Values that are documentation placeholders or code references, not secrets.
_PLACEHOLDER_PREFIXES = (
    "your",
    "my",
    "some",
    "the",
    "test",
    "dummy",
    "sample",
    "example",
    "placeholder",
    "changeme",
    "change_me",
    "redacted",
    "none",
    "null",
    "true",
    "false",
    "todo",
    "secret",
    "token",
    "key",
    "value",
    "password",
    "os",
    "process",
    "sys",
    "env",
)


def _looks_like_literal_secret(value: str) -> bool:
    """Reject placeholders, identifiers and template references.

    A real credential in documentation is a literal string: it carries a digit,
    has some character variety, and is not a SCREAMING_SNAKE_CASE identifier.
    """
    lowered = value.lower()
    if any(lowered.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES):
        return False
    if not any(ch.isdigit() for ch in value):
        return False
    if value.isdigit():
        return False
    if len(set(value)) < 5:
        return False
    if value.isupper() and "_" in value:
        return False
    return True


def _embedded_secret_hits(text: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for match in _SECRET_ASSIGN_RE.finditer(text):
        if not _looks_like_literal_secret(match.group("value")):
            continue
        hits.append((match.start(), match.group(0)))
    return hits


# A credential path only matters when the same line actually reads the file:
# a library call (``open``/``read_text``), a shell reader (``cat``) or a
# request that streams a local file (``@path`` / ``--upload-file``).
# Documentation that merely mentions ``.env`` / ``.ssh`` is not exfiltration.
_FILE_READ_RE = re.compile(r"(?i)(?:\bopen\s*\(|\bread_text\b|\bread_bytes\b|\bcat\s+|\bshutil\.copy\b|--upload-file\b|\s-T\s|@\s*[\"']?[.~/$])")

# Pipeline targets that only pretty-print or format data.  ``curl ... | python
# -m json.tool`` does not execute the downloaded bytes, so it must not be
# treated as download-and-execute.
_PIPE_FORMATTER_RE = re.compile(
    r"\|\s*(?:sudo\s+)?(?:python[0-9.]*\s+-m\s+(?:json\.tool|yq)|jq|yq|column\s+-t)\b",
    re.IGNORECASE,
)

# ``curl ... | python3 -c "<inline script>"`` runs local code over downloaded
# data; the payload is never executed.
_INLINE_SCRIPT_RE = re.compile(
    r"\|\s*(?:sudo\s+)?(?:python[0-9.]*|node|ruby|perl)\s+(?:-c|-e|--eval)\b",
    re.IGNORECASE,
)

# Decoders whose output is only dangerous when it reaches an execution sink.
_DECODE_CALL_RE = re.compile(
    r"(?:base64\.(?:b64decode|decodebytes|standard_b64decode|urlsafe_b64decode)|"
    r"bytes\.fromhex|codecs\.decode|atob)\s*\(",
    re.IGNORECASE,
)
_SHELL_DECODE_RE = re.compile(r"\bbase64\s+(?:-d|--decode)\b", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(r"(?:\b(?:const|let|var)\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<rhs>[^\n]*)")


@dataclass(frozen=True, slots=True)
class Finding:
    """A single rule hit.  ``excerpt_hash`` replaces the raw snippet."""

    rule_id: str
    severity: Severity
    line: int | None
    message: str
    excerpt_hash: str

    def as_dict(self, *, file: str) -> dict:
        return {
            "code": self.rule_id,
            "severity": self.severity,
            "file": file,
            "line": self.line,
            "message": self.message,
            "excerpt_hash": self.excerpt_hash,
            "source": "rules",
        }


@dataclass(frozen=True, slots=True)
class _Rule:
    rule_id: str
    message: str
    patterns: tuple[re.Pattern[str], ...]
    severity: Severity = "block"
    # Block rules are downgraded to ``warn`` when the matching line explicitly
    # tells the reader not to do it ("never run ...", "禁止执行 ...").
    negation_downgrade: bool = True
    applies_to: Literal["always", "script"] = "always"
    # A match is discarded when the rest of its line matches one of these
    # guards, e.g. a download piped into a formatter instead of an interpreter.
    tail_guards: tuple[re.Pattern[str], ...] = ()


def _rx(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_RULES: tuple[_Rule, ...] = (
    # --- download and execute -------------------------------------------
    _Rule(
        rule_id="download_and_execute",
        message="Content downloads remote code and executes it in one step.",
        severity="block",
        patterns=_rx(
            r"\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b",
            r"\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?python[0-9.]*",
            r"\b(?:iwr|invoke-webrequest|invoke-restmethod)\b[^\n]{0,200}\|\s*(?:iex|invoke-expression)\b",
            r"\bnc\s+[^\n]*\s-e\s+\S+",
            r"\b(?:bash|sh)\s+-i\s*>&\s*/dev/tcp/",
        ),
        # Piping into jq / `python -m json.tool` / `column -t` only formats, and
        # `python3 -c "..."` / `node -e "..."` runs local code over the data.
        tail_guards=(_PIPE_FORMATTER_RE, _INLINE_SCRIPT_RE),
    ),
    # --- credential access ----------------------------------------------
    _Rule(
        rule_id="credential_path_access",
        message="Content references a private credential or key location.",
        severity="block",
        patterns=_rx(
            r"~/\.ssh\b",
            r"\bid_(?:rsa|ed25519|ecdsa|dsa)\b",
            r"~/\.aws/(?:credentials|config)\b",
            r"\.aws/credentials\b",
            r"~/\.netrc\b|\b\.netrc\b",
            r"~/\.npmrc\b|\b\.npmrc\b",
            r"~/\.pypirc\b",
            r"/etc/shadow\b",
            r"/etc/passwd\b",
            r"~/\.kube/config\b",
            r"~/\.docker/config\.json\b",
            r"Library/Keychains\b",
            r"\bCookies\.sqlite\b|\bLogin Data\b",
            r"~/\.config/gh/hosts\.yml\b",
        ),
    ),
    # --- obfuscation / encoded execution --------------------------------
    # Decoding on its own is normal (images, audio, docx payloads).  Only a
    # decode that reaches an execution sink is blocking; the "decode into a
    # variable, execute the variable later" shape is handled by
    # :func:`_encoded_execution_hits`.
    _Rule(
        rule_id="obfuscated_execution",
        message="Content decodes or de-obfuscates data and then executes it.",
        severity="block",
        patterns=_rx(
            r"\beval\s*\(\s*(?:compile|base64|bytes\.fromhex|chr|atob)\b",
            r"\bexec\s*\(\s*(?:compile|base64|bytes\.fromhex|__import__|atob)\b",
            r"\b__import__\s*\(\s*(?:base64|codecs|marshal|zlib)\b",
            r"\b(?:pickle|marshal)\.loads\s*\(",
            r"\b(?:base64\s+(?:-d|--decode)|atob)\b[^\n]{0,200}\|\s*(?:ba|z|da)?sh\b",
            r"\b(?:base64\s+(?:-d|--decode)|atob)\b[^\n]{0,200}\|\s*python[0-9.]*",
            r"(?:\\x[0-9a-f]{2}){12,}",
            r"\bchr\(\s*\d+\s*\)\s*\+",
        ),
    ),
    # --- destructive commands -------------------------------------------
    _Rule(
        rule_id="destructive_command",
        message="Content contains a command that can destroy data or the host.",
        severity="block",
        patterns=_rx(
            r"\brm\s+-[a-z]*[rf][a-z]*\s+(?:/|/\*|~|\$HOME)(?=\s|$|[`\"')\].,;:])",
            r"\bmkfs(?:\.[a-z0-9]+)?\b",
            r"\bdd\b[^\n]{0,80}\bof=/dev/(?:sd|nvme|hd)",
            # Only real shutdown invocations, not identifiers like
            # ``/api/shutdown`` or ``def shutdown()``.
            r"\bshutdown\s+(?:-[hrHPc]\b|--(?:halt|poweroff|reboot)\b|now\b|\+\d+\b|\d+\b)",
            r"\bsystemctl\s+(?:poweroff|reboot|halt|kexec)\b",
            r"(?m)^\s*(?:sudo\s+)?(?:reboot|poweroff|halt|shutdown)\s*;?\s*$",
            r"\bDROP\s+(?:TABLE|DATABASE)\b",
            r"\bgit\s+push\b[^\n]*--force\b",
            # Command position only: PowerPoint transition names ("shred",
            # `p14:shred dir=out`, markdown table cells) must not match.
            r"(?m)(?:^|[;&|])\s*(?:sudo\s+)?shred\s+[^\s|=\"']",
            r":\(\)\s*\{\s*:\|:&\s*\};:",
        ),
    ),
    # --- privilege escalation -------------------------------------------
    # `sudo` alone only warns: tutorial install steps ("sudo apt install ...")
    # are common in legitimate skills.  The patterns below are the escalation
    # shapes that persist privilege or rewrite access control, so they stay
    # blocking.
    _Rule(
        rule_id="privilege_escalation",
        message="Content attempts to persist elevated privileges or rewrite access control.",
        severity="block",
        patterns=_rx(
            r"\bchown\s+(?:-R\s+)?root\b",
            r"/etc/sudoers\b",
            r"\bsetuid\b",
            r"\bsetcap\b",
            r"\bchmod\s+[0-7]*7[0-7]{2}\s+/(?:etc|usr|bin|sbin)\b",
        ),
    ),
    _Rule(
        rule_id="sudo_usage",
        message="Content uses sudo; verify the command is expected in this context.",
        severity="warn",
        patterns=_rx(r"\bsudo\s+"),
    ),
    # --- writes outside the skill tree ----------------------------------
    _Rule(
        rule_id="path_escape_write",
        message="Content writes outside the skill directory (system or credential paths).",
        severity="block",
        patterns=_rx(
            r"(?:>>?|\btee\s+(?:-a\s+)?)\s*(?:/etc/|/usr/|/bin/|/sbin/|/root/|~/\.ssh/)",
            r"\bopen\s*\(\s*['\"](?:/etc/|/usr/|/root/|~/\.ssh/)",
        ),
    ),
    # --- known exfiltration sinks ---------------------------------------
    _Rule(
        rule_id="exfiltration_sink",
        message="Content points at a known data-exfiltration endpoint.",
        severity="block",
        patterns=_rx(
            r"(?:discord(?:app)?\.com/api/webhooks|api\.telegram\.org/bot|hooks\.slack\.com/services)",
            r"(?:webhook\.site|requestbin|pipedream\.net|ngrok\.(?:io|app|free))",
            r"(?:pastebin\.com|transfer\.sh|0x0\.st|file\.io|gofile\.io)",
            r"(?:burpcollaborator|interact\.sh|oast\.(?:fun|pro|me)|canarytokens)",
        ),
    ),
    _Rule(
        rule_id="file_upload_command",
        message="A script uploads a local file to a remote endpoint.",
        # Uploading a single referenced asset is normal.  Archive uploads are
        # escalated by ``external_egress_unknown`` when the payload is bulk data.
        severity="warn",
        applies_to="script",
        patterns=_rx(
            r"\bcurl\b[^\n]{0,200}(?:-F|--form|--upload-file|-T)\b[^\n]{0,200}(?:@|~/|\$HOME)",
            r"\b(?:requests|httpx)\.(?:post|put)\s*\([^\n]{0,200}files\s*=",
        ),
    ),
    # --- secrets embedded in content ------------------------------------
    _Rule(
        rule_id="embedded_secret",
        message=_EMBEDDED_SECRET_MESSAGE,
        severity="warn",
        patterns=_rx(
            r"\bsk-[A-Za-z0-9]{16,}",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"\bghp_[A-Za-z0-9]{20,}",
            r"\bxox[baprs]-[A-Za-z0-9-]{10,}",
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        ),
    ),
    # --- semantic-ish heuristics (warn only, by design) ------------------
    _Rule(
        rule_id="conditional_instruction",
        message="Content contains a conditional instruction that could redirect user data.",
        severity="warn",
        patterns=_rx(
            r"(?:当|如果|一旦|when|whenever|if)[^\n]{0,40}(?:用户|user)[^\n]{0,40}"
            r"(?:问|询问|要求|asks?|requests?)[^\n]{0,200}"
            r"(?:发送|发给|上传|转发|同步|send|post|upload|forward)[^\n]{0,120}"
            r"(?:https?://|邮箱|email|webhook|telegram|discord)",
        ),
    ),
    _Rule(
        rule_id="prompt_injection_phrase",
        message="Content uses prompt-injection wording that tries to override the agent.",
        severity="warn",
        patterns=_rx(
            r"ignore\s+(?:all\s+)?(?:the\s+)?previous\s+instructions?",
            r"disregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior)\s+(?:instructions?|prompts?)",
            r"you\s+are\s+now\s+(?:a|an|the)\b",
            r"override\s+(?:the\s+)?system\s+(?:prompt|instructions?)",
            r"(?:忽略|无视|忘记)(?:之前|以上|前面|先前)的?(?:所有)?(?:指令|指示|提示)",
            r"(?:你现在是|从现在开始你是)",
            r"系统提示词",
        ),
    ),
    _Rule(
        rule_id="hidden_user_instruction",
        message="Content tries to keep an instruction hidden from the end user.",
        severity="warn",
        patterns=_rx(
            r"(?:do not|don't|不要|无需|不必)[^\n]{0,20}"
            r"(?:告诉|告知|通知|tell|inform|notify)[^\n]{0,20}(?:用户|user)",
            r"(?:hide|hidden|隐藏)[^\n]{0,20}(?:from|对)[^\n]{0,20}(?:user|用户)",
        ),
    ),
    _Rule(
        rule_id="html_comment_instruction",
        message="An HTML comment contains instruction-like text.",
        severity="warn",
        patterns=_rx(
            r"<!--(?:(?!-->).){0,400}?\b(?:ignore|system|always|send|must|不要|发送|永远)\b"
            r"(?:(?!-->).){0,400}?-->",
        ),
    ),
)

_URL_RE = re.compile(r"https?://[^\s)\]\"'<>`]+", re.IGNORECASE)


def _excerpt_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:16]


def _normalize(content: str) -> str:
    """Normalise the text while keeping the line structure intact."""
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text)
    return _ZERO_WIDTH_RE.sub("", text)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _is_negated(lines: list[str], line_number: int) -> bool:
    if not 1 <= line_number <= len(lines):
        return False
    lowered = lines[line_number - 1].lower()
    return any(marker in lowered for marker in _NEGATION_MARKERS)


def _host_allowed(host: str | None) -> bool:
    if not host:
        return True
    host = host.lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWLISTED_HOST_SUFFIXES)


def _line_tail(text: str, offset: int) -> str:
    newline = text.find("\n", offset)
    return text[offset:] if newline == -1 else text[offset:newline]


def _is_dynamic_url(text: str, start: int, raw: str) -> bool:
    """Return True for an f-string / interpolated URL.

    Such a URL has an assembled value, so the literal host may not be where the
    data actually goes; it is surfaced as a warning instead of a hard block.
    """
    if "{" in raw or "}" in raw:
        return True
    if re.search(r"%[sd(]", raw):
        return True
    prefix = text[max(0, start - 4) : start]
    return bool(re.search(r"""[fF](?:[rR]|[bB])?["']$""", prefix))


def _carries_sensitive_payload(text: str) -> bool:
    """True when a file sends bulk data or freshly-read local secrets.

    Ordinary API calls (a prompt, an image, an auth header) are not sensitive;
    uploading an archive, dumping the environment, or shipping credential /
    browser-store contents is.
    """
    if _ARCHIVE_BUILD_RE.search(text):
        return True
    for line in text.splitlines():
        if _SENSITIVE_FILE_RE.search(line) and _FILE_READ_RE.search(line):
            return True
    if _ENV_DUMP_RE.search(text):
        return True
    for match in _ENV_ASSIGN_RE.finditer(text):
        name = re.escape(match.group("name"))
        if re.search(rf"(?i)\b(?:data|json|params|files|body)\s*=\s*[^\n]*\b{name}\b", text):
            return True
    return False


def _encoded_execution_hits(text: str) -> list[tuple[int, str]]:
    """Find decoded data that is later executed through a variable.

    Covers the shape the regex layer cannot express::

        payload = base64.b64decode(blob)
        exec(payload)

    and the shell/JS equivalents (``$PAYLOAD`` after ``base64 -d``).
    """
    decoded_names: list[tuple[str, int]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        line_end = offset + len(line)
        if _DECODE_CALL_RE.search(line) or _SHELL_DECODE_RE.search(line):
            assignment = _ASSIGNMENT_RE.search(line)
            if assignment is not None:
                decoded_names.append((assignment.group("name"), line_end))
        offset = line_end

    hits: list[tuple[int, str]] = []
    for name, offset in decoded_names:
        escaped = re.escape(name)
        # ``(?<![.\w])`` keeps ``re.compile(pattern)`` or ``self.exec(x)`` out:
        # those are library calls on decoded data, not execution of it.
        python_sink = re.compile(
            rf"(?<![.\w])(?:exec|eval|compile|pickle\.loads|marshal\.loads)\s*\(\s*{escaped}\b",
            re.IGNORECASE,
        )
        shell_sink = re.compile(
            rf"\b(?:eval|bash|sh|python[0-9.]*)\b[^\n]{{0,20}}\$\{{?{escaped}\b",
            re.IGNORECASE,
        )
        for sink in (python_sink, shell_sink):
            match = sink.search(text, offset)
            if match is not None:
                hits.append((match.start(), match.group(0)))
    return hits


def scan_content_rules(
    content: str,
    *,
    location: str,
    executable: bool = False,
) -> list[Finding]:
    """Return deterministic findings for one skill file.

    ``location`` is only used to decide whether the file is prose/instructions
    or a script when a rule is tier-sensitive.  The caller prefixes findings
    with the file path when building the API response.
    """
    if not content:
        return []

    text = _normalize(content)
    lines = text.splitlines()
    findings: dict[tuple[str, int], Finding] = {}

    if _ZERO_WIDTH_RE.search(content):
        findings[("zero_width_characters", 0)] = Finding(
            rule_id="zero_width_characters",
            severity="warn",
            line=None,
            message="Content contains zero-width characters that could hide instructions.",
            excerpt_hash=_excerpt_hash("\u200b"),
        )

    for rule in _RULES:
        if rule.applies_to == "script" and not executable:
            continue
        for pattern in rule.patterns:
            for match in pattern.finditer(text):
                if rule.tail_guards and any(guard.search(_line_tail(text, match.start())) for guard in rule.tail_guards):
                    continue
                line_number = _line_of(text, match.start())
                severity: Severity = rule.severity
                if severity == "block" and rule.negation_downgrade and _is_negated(lines, line_number):
                    severity = "warn"
                key = (rule.rule_id, line_number)
                previous = findings.get(key)
                if previous is not None and previous.severity == "block":
                    continue
                findings[key] = Finding(
                    rule_id=rule.rule_id,
                    severity=severity,
                    line=line_number,
                    message=rule.message,
                    excerpt_hash=_excerpt_hash(match.group(0)),
                )

    # Decoded data that later reaches exec/eval/compile: the decode itself is
    # not a finding, the execution of its result is.
    for offset, snippet in _encoded_execution_hits(text):
        line_number = _line_of(text, offset)
        key = ("obfuscated_execution", line_number)
        previous = findings.get(key)
        if previous is not None and previous.severity == "block":
            continue
        severity = "block"
        if _is_negated(lines, line_number):
            severity = "warn"
        findings[key] = Finding(
            rule_id="obfuscated_execution",
            severity=severity,
            line=line_number,
            message="Content decodes or de-obfuscates data and then executes it.",
            excerpt_hash=_excerpt_hash(snippet),
        )

    # Plain ``name = literal`` secrets.  Values that are placeholders, env
    # lookups or template braces are filtered out by the validator above.
    for offset, snippet in _embedded_secret_hits(text):
        line_number = _line_of(text, offset)
        key = ("embedded_secret", line_number)
        if key in findings:
            continue
        findings[key] = Finding(
            rule_id="embedded_secret",
            severity="warn",
            line=line_number,
            message=_EMBEDDED_SECRET_MESSAGE,
            excerpt_hash=_excerpt_hash(snippet),
        )

    # Outbound hosts: an unknown host is a warning.  It blocks only when the
    # payload is sensitive (archive, whole environment, credential/browser
    # stores); the destination does not matter then.
    script_sends_data = executable and bool(_NETWORK_SEND_RE.search(text))
    sends_sensitive = script_sends_data and _carries_sensitive_payload(text)
    for match in _URL_RE.finditer(text):
        host = urlsplit(match.group(0)).hostname
        if _host_allowed(host):
            continue
        line_number = _line_of(text, match.start())
        if not executable:
            # Documentation legitimately links to the outside world.  Only flag
            # a link in prose when the same line talks about sending data.
            line_text = lines[line_number - 1].lower() if line_number <= len(lines) else ""
            if not re.search(
                r"(发送|发到|上传|转发|同步|send|post|upload|forward|webhook|curl|requests)",
                line_text,
            ):
                continue
        key = ("external_egress_unknown", line_number)
        if key in findings:
            continue
        dynamic_url = _is_dynamic_url(text, match.start(), match.group(0))
        blocking = sends_sensitive
        if blocking:
            message = "A script sends local secrets or an archive to a host that is not on the allowlist."
        elif dynamic_url:
            message = "A script sends data to a destination assembled at runtime; verify the real host."
        else:
            message = "Content references an external host that is not on the allowlist."
        findings[key] = Finding(
            rule_id="external_egress_unknown",
            severity="block" if blocking else "warn",
            line=line_number,
            message=message,
            excerpt_hash=_excerpt_hash(match.group(0)),
        )

    ordered = sorted(findings.values(), key=lambda item: (item.line or 0, item.rule_id))
    return ordered[:MAX_FINDINGS_PER_FILE]


def has_blocking_finding(findings: list[Finding]) -> bool:
    return any(finding.severity == "block" for finding in findings)
