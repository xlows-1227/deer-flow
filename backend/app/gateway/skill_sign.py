"""Skill API sign — base64 encoding for external skill endpoints.

The sign is a base64-encoded string whose plaintext is ``psid|timestamp|key``
where *timestamp* is a Unix epoch integer (UTC seconds) and *key* is the shared
secret.  Callers generate the sign on their side, and the gateway decodes it to
verify:

1. The key inside the sign matches the server's ``SKILL_API_SIGN_KEY``.
2. The psid inside the sign matches the psid query parameter.
3. The request is within 30 minutes of the embedded timestamp.

Using base64 (instead of Fernet) ensures every language (Java, PHP, JS, Go…)
can generate the sign with native standard libraries — no third-party deps.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SIGN_KEY_ENV = "SKILL_API_SIGN_KEY"
_MAX_AGE_SECONDS = 30 * 60  # 30 minutes

# Development-only fixed key (NOT for production).
_DEV_KEY = "deerflow-dev-sign-key"


def _load_key() -> str:
    key = os.getenv(_SIGN_KEY_ENV)
    if key:
        return key
    logger.warning(
        "%s is not set. Using a development-only fixed key. "
        "Set it in production.",
        _SIGN_KEY_ENV,
    )
    return _DEV_KEY


@dataclass(frozen=True)
class SignPayload:
    """Decoded sign contents."""

    psid: str
    timestamp: int


def generate_sign(psid: str, timestamp: int | None = None) -> str:
    """Generate a base64-encoded sign string for the given psid.

    Args:
        psid: The user's PSID (email).
        timestamp: Unix epoch seconds (UTC). Defaults to current time.

    Returns:
        Base64-encoded sign string (URL-safe).
    """
    if timestamp is None:
        timestamp = int(time.time())
    key = _load_key()
    plaintext = f"{psid}|{timestamp}|{key}"
    return base64.urlsafe_b64encode(plaintext.encode()).decode()


def verify_sign(sign: str, psid: str) -> SignPayload:
    """Decode and validate the sign.

    Args:
        sign: The base64-encoded sign string.
        psid: The psid from the request query parameter, to match against.

    Returns:
        The decoded :class:`SignPayload`.

    Raises:
        ValueError: If the sign is invalid, key/psid does not match, or the
            sign has expired (older than 30 minutes).
    """
    if not sign:
        raise ValueError("Missing sign parameter")

    try:
        plaintext = base64.urlsafe_b64decode(sign.encode()).decode()
    except Exception as exc:
        raise ValueError("Invalid sign: decode failed") from exc

    parts = plaintext.split("|")
    if len(parts) != 3:
        raise ValueError("Invalid sign: malformed payload")

    sign_psid, ts_str, sign_key = parts

    # 1. Verify key
    if sign_key != _load_key():
        raise ValueError("Invalid sign: key mismatch")

    # 2. Verify psid
    if sign_psid != psid:
        raise ValueError("Sign psid does not match the provided psid")

    # 3. Verify timestamp
    try:
        sign_ts = int(ts_str)
    except ValueError:
        raise ValueError("Invalid sign: malformed timestamp")

    now = int(time.time())
    if abs(now - sign_ts) > _MAX_AGE_SECONDS:
        raise ValueError("Sign has expired (timestamp exceeds 30-minute window)")

    return SignPayload(psid=sign_psid, timestamp=sign_ts)
