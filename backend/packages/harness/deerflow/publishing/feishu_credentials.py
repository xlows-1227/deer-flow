"""Versioned encrypted payload format for one Feishu binding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_PAYLOAD_VERSION = 1


class FeishuCredentialPayloadError(ValueError):
    """Raised when a SecretStore value is not a valid Feishu credential bundle."""


@dataclass(frozen=True)
class FeishuCredentials:
    """Secret material needed to authenticate a dynamic Feishu binding."""

    app_secret: str
    verification_token: str
    encrypt_key: str = ""


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise FeishuCredentialPayloadError(f"Feishu credential payload is missing {field}")
    return value.strip()


def encode_feishu_credentials(credentials: FeishuCredentials) -> str:
    """Serialize one credential bundle before encrypting it in SecretStore."""
    app_secret = credentials.app_secret.strip()
    verification_token = credentials.verification_token.strip()
    if not app_secret or not verification_token:
        raise FeishuCredentialPayloadError("app_secret and verification_token are required")
    return json.dumps(
        {
            "version": _PAYLOAD_VERSION,
            "app_secret": app_secret,
            "verification_token": verification_token,
            "encrypt_key": credentials.encrypt_key.strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_feishu_credentials(value: str) -> FeishuCredentials:
    """Validate and deserialize a decrypted SecretStore credential bundle."""
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise FeishuCredentialPayloadError("Feishu credential payload is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("version") != _PAYLOAD_VERSION:
        raise FeishuCredentialPayloadError("Unsupported Feishu credential payload version")
    encrypt_key = payload.get("encrypt_key", "")
    if not isinstance(encrypt_key, str):
        raise FeishuCredentialPayloadError("Feishu credential payload has an invalid encrypt_key")
    return FeishuCredentials(
        app_secret=_required_string(payload, "app_secret"),
        verification_token=_required_string(payload, "verification_token"),
        encrypt_key=encrypt_key.strip(),
    )


__all__ = [
    "FeishuCredentialPayloadError",
    "FeishuCredentials",
    "decode_feishu_credentials",
    "encode_feishu_credentials",
]
