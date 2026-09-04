from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    database_url_test: str = ""

    blob_backend: Literal["local", "s3"] = "local"
    blob_root: str = "./var/blobs"
    s3_bucket: str = "cs-blobs"
    s3_endpoint_url: str | None = None

    auth_mode: Literal["dev", "clerk"] = "clerk"
    clerk_jwks_url: str = ""
    clerk_issuer: str = ""

    token_encryption_key: str
    public_base_url: str = "http://localhost:8000"

    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "http://localhost:8000/meta/oauth/callback"
    meta_api_version: str = "v21.0"

    anthropic_api_key: str = ""
    claude_model_tagging: str = "claude-opus-5"
    claude_model_insights: str = "claude-opus-5"
    claude_model_copilot: str = "claude-opus-5"
    claude_model_copy: str = "claude-opus-5"

    ai_gateway_api_key: str = ""
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh"
    image_model: str = "bfl/flux-2-pro"
    video_model: str = "klingai/kling-v3.0-i2v"
    transcription_model: str = "openai/whisper-1"
    embedding_model: str = "openai/text-embedding-3-small"

    worker_poll_seconds: float = 1.0
    job_lease_seconds: int = 600

    @model_validator(mode="after")
    def _check_clerk_config(self) -> "Settings":
        if self.auth_mode == "clerk" and not self.clerk_jwks_url:
            raise ValueError("CLERK_JWKS_URL is required when AUTH_MODE=clerk")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
