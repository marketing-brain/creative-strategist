from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    database_url_test: str = ""

    blob_backend: Literal["local", "s3"] = "local"
    blob_root: str = "./var/blobs"
    s3_bucket: str = "cs-blobs"
    s3_endpoint_url: str | None = None
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_default_region: str = "us-east-1"

    # No dev-mode auth bypass: every environment, including local development,
    # authenticates through Clerk. A test-only auth override lives in
    # tests/conftest.py, wired through FastAPI's dependency_overrides -- never
    # through a Settings flag the running app could be misconfigured with.
    clerk_jwks_url: str
    clerk_issuer: str = ""

    public_base_url: str = "http://localhost:8000"

    fivetran_api_key: str = ""
    fivetran_api_secret: str = ""
    fivetran_group_id: str = ""
    web_base_url: str = "http://localhost:3000"

    anthropic_api_key: str = ""
    claude_model_insights: str = "claude-opus-5"
    claude_model_copilot: str = "claude-opus-5"
    claude_model_copy: str = "claude-opus-5"

    ai_gateway_api_key: str = ""
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh"
    tagging_model: str = "google/gemini-2.5-flash-lite"
    image_model: str = "bfl/flux-2-pro"
    video_model: str = "klingai/kling-v3.0-i2v"
    transcription_model: str = "openai/whisper-1"
    embedding_model: str = "openai/text-embedding-3-small"

    worker_poll_seconds: float = 1.0
    job_lease_seconds: int = 600

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
