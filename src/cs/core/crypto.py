from cryptography.fernet import Fernet

from cs.core.config import Settings


def _fernet(settings: Settings) -> Fernet:
    return Fernet(settings.token_encryption_key.encode())


def encrypt_str(settings: Settings, plaintext: str) -> str:
    return _fernet(settings).encrypt(plaintext.encode()).decode()


def decrypt_str(settings: Settings, token: str) -> str:
    return _fernet(settings).decrypt(token.encode()).decode()
