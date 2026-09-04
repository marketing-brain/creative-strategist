from cs.core.config import Settings


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setenv("DATABASE_URL_TEST", "postgres://y")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
    s = Settings()
    assert s.database_url == "postgres://x"
    assert s.blob_backend == "local"
    assert s.claude_model_tagging == "claude-opus-5"
    assert s.meta_api_version == "v21.0"
