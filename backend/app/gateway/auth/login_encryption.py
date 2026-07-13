"""Temporary application-layer encryption for login passwords.

This is a compatibility measure for deployments that cannot enable TLS
immediately.  It prevents a plaintext password from appearing in the HTTP
request body, but it does not protect against an active man-in-the-middle
attacker that can replace the public key or frontend JavaScript.
"""

from __future__ import annotations

import base64
import binascii
import os
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ENCRYPTED_PASSWORD_PREFIX = "rsa-oaep-sha256:"


class LoginEncryptionError(ValueError):
    """Raised when login encryption is required or decryption fails."""


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def login_encryption_required() -> bool:
    return _env_flag("AUTH_LOGIN_ENCRYPTION_REQUIRED")


@lru_cache(maxsize=1)
def get_login_private_key() -> rsa.RSAPrivateKey | None:
    """Load the shared RSA private key from a file or an environment value."""
    key_file = os.getenv("AUTH_LOGIN_RSA_PRIVATE_KEY_FILE", "").strip()
    key_value = os.getenv("AUTH_LOGIN_RSA_PRIVATE_KEY", "").strip()

    if key_file:
        pem = Path(key_file).read_bytes()
    elif key_value:
        # Allows a PEM to be stored as a single-line ``\n`` escaped env value.
        pem = key_value.replace("\\n", "\n").encode("utf-8")
    else:
        return None

    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise RuntimeError("AUTH_LOGIN_RSA_PRIVATE_KEY must contain an RSA private key")
    if key.key_size < 2048:
        raise RuntimeError("Login encryption RSA key must be at least 2048 bits")
    return key


def get_login_public_key_pem() -> str | None:
    private_key = get_login_private_key()
    if private_key is None:
        return None
    return (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def decrypt_login_password(value: str) -> str:
    """Return a plaintext password from a prefixed ciphertext or legacy value."""
    if not value.startswith(ENCRYPTED_PASSWORD_PREFIX):
        if login_encryption_required():
            raise LoginEncryptionError("Encrypted login password is required")
        return value

    private_key = get_login_private_key()
    if private_key is None:
        raise LoginEncryptionError("Login encryption is not configured")

    encoded = value[len(ENCRYPTED_PASSWORD_PREFIX) :]
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
        # Reject malformed lengths before invoking the asymmetric primitive.
        if len(ciphertext) != private_key.key_size // 8:
            raise LoginEncryptionError("Invalid encrypted login password")
        plaintext = private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise LoginEncryptionError("Invalid encrypted login password") from exc

    if not plaintext:
        raise LoginEncryptionError("Invalid encrypted login password")
    return plaintext
