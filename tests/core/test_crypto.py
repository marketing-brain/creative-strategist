from cryptography.fernet import Fernet

from cs.core.config import Settings
from cs.core.crypto import decrypt_str, encrypt_str


def test_roundtrip(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "x")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    s = Settings()
    token = encrypt_str(s, "secret-token")
    assert token != "secret-token"
    assert decrypt_str(s, token) == "secret-token"
