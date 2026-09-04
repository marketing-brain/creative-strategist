import pytest
from pydantic import ValidationError

from cs.core.config import Settings


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setenv("DATABASE_URL_TEST", "postgres://y")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("CLERK_JWKS_URL", "")


def test_settings_reads_env():
    s = Settings()
    assert s.database_url == "postgres://x"
    assert s.blob_backend == "local"
    assert s.claude_model_tagging == "claude-opus-5"
    assert s.meta_api_version == "v21.0"


def test_auth_mode_defaults_to_clerk():
    assert Settings.model_fields["auth_mode"].default == "clerk"


def test_clerk_mode_requires_jwks_url():
    with pytest.raises(ValidationError, match="CLERK_JWKS_URL is required"):
        Settings(auth_mode="clerk", clerk_jwks_url="")


def test_clerk_mode_with_jwks_url_is_valid():
    s = Settings(auth_mode="clerk", clerk_jwks_url="https://example.test/jwks")
    assert s.clerk_jwks_url == "https://example.test/jwks"


def test_dev_mode_without_jwks_url_is_valid():
    s = Settings(auth_mode="dev", clerk_jwks_url="")
    assert s.auth_mode == "dev"
