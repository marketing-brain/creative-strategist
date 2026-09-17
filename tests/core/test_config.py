import pytest
from pydantic import ValidationError

from cs.core.config import Settings


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setenv("DATABASE_URL_TEST", "postgres://y")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/jwks")


def test_settings_reads_env():
    s = Settings()
    assert s.database_url == "postgres://x"
    assert s.blob_backend == "local"
    assert s.tagging_model == "google/gemini-2.5-flash-lite"
    # the merge judge reasons over short text but must be precise: the full flash tier
    assert s.judge_model == "google/gemini-2.5-flash"


def test_clerk_jwks_url_is_required(monkeypatch):
    """No dev-mode bypass: every environment authenticates through Clerk, so
    CLERK_JWKS_URL has no default and is required the same way DATABASE_URL is."""
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    with pytest.raises(ValidationError, match="clerk_jwks_url"):
        Settings(_env_file=None)
