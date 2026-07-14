import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.gateway.auth import login_encryption


@pytest.fixture
def login_key_file(tmp_path, monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_file = tmp_path / "login-key.pem"
    key_file.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("AUTH_LOGIN_RSA_PRIVATE_KEY_FILE", str(key_file))
    monkeypatch.delenv("AUTH_LOGIN_RSA_PRIVATE_KEY", raising=False)
    login_encryption.get_login_private_key.cache_clear()
    yield private_key
    login_encryption.get_login_private_key.cache_clear()


def test_encrypted_password_round_trip(login_key_file):
    ciphertext = login_key_file.public_key().encrypt(
        b"correct horse battery staple",
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    request_value = login_encryption.ENCRYPTED_PASSWORD_PREFIX + base64.b64encode(ciphertext).decode("ascii")

    assert login_encryption.decrypt_login_password(request_value) == "correct horse battery staple"


def test_plaintext_is_allowed_by_default(monkeypatch):
    monkeypatch.delenv("AUTH_LOGIN_ENCRYPTION_REQUIRED", raising=False)
    assert login_encryption.decrypt_login_password("legacy-password") == "legacy-password"


def test_plaintext_is_rejected_when_encryption_required(monkeypatch):
    monkeypatch.setenv("AUTH_LOGIN_ENCRYPTION_REQUIRED", "true")
    with pytest.raises(login_encryption.LoginEncryptionError, match="required"):
        login_encryption.decrypt_login_password("legacy-password")


def test_public_key_never_contains_private_material(login_key_file):
    public_pem = login_encryption.get_login_public_key_pem()
    assert public_pem is not None
    assert "BEGIN PUBLIC KEY" in public_pem
    assert "PRIVATE" not in public_pem


def test_invalid_ciphertext_is_rejected(login_key_file):
    request_value = login_encryption.ENCRYPTED_PASSWORD_PREFIX + base64.b64encode(b"too-short").decode("ascii")
    with pytest.raises(login_encryption.LoginEncryptionError, match="Invalid"):
        login_encryption.decrypt_login_password(request_value)
