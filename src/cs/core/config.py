from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    database_url_test: str = ""
    # Optional URL for the long-lived app pools (api, worker, scheduler). Point it at the
    # transaction-mode pooler (Supabase port 6543) so the three processes share server
    # connections instead of each pinning a session-mode slot; migrations and psql keep
    # using DATABASE_URL. Empty means "same as DATABASE_URL".
    database_pool_url: str = ""
    # Longest any one API query may run before Postgres cancels it. 0 turns the bound off.
    api_statement_timeout_ms: int = 15000

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

    ai_gateway_api_key: str = ""
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh"
    tagging_model: str = "google/gemini-2.5-flash-lite"
    # Separate from tagging_model: video needs native video-file input (frames + audio
    # together, verified against a real AI Gateway call), and flash-lite's video support
    # isn't documented separately from full flash -- safer to keep lite for images only.
    video_tagging_model: str = "google/gemini-2.5-flash"
    # Kill switch for video tagging cost specifically -- image/carousel tagging (cheap,
    # flash-lite) is unaffected. False stops new video downloads from enqueueing a
    # tagging.tag_creative job at all; it does not touch jobs already in the queue.
    video_tagging_enabled: bool = True
    insights_model: str = "google/gemini-2.5-flash-lite"
    # Open-vocabulary merge (spec 2026-09-16-vocab-merge-design.md): slug embeddings for
    # candidate recall. pgvector HNSW caps at 2000 dims; 1536 keeps the index option open.
    embedding_model: str = "google/gemini-embedding-2"
    embedding_dimensions: int = 1536
    # The merge judge is text-only and cheap either way, but flash-lite over-merged in
    # dry run 1 (ingredient vs. branded form, falling asleep vs. sleep quality). A wrong
    # merge is destructive and only reversible by hand, so it runs on the full flash tier.
    judge_model: str = "google/gemini-2.5-flash"
    # Account read (spec 2026-09-17). Grouping dimensions for bounded interaction tests;
    # a grouping value needs account_read_min_units units before it is used as a group.
    account_read_grouping_dims: str = (
        "audience_callouts,products_mentioned,features_mentioned,messenger,duration_s"
    )
    account_read_min_units: int = 10
    account_read_permutation_draws: int = 1000
    # The account read's writer. It reasons over a long brief of graded comparisons, so the
    # stronger tier.
    account_read_model: str = "google/gemini-3-flash"

    # Fallback for videos Fivetran's Meta connector can never expose: Partnership Ads,
    # where the video lives in the creator's own Instagram account rather than the
    # advertiser's ad-account video library. Empty means the feature is simply off --
    # meta.resolve_instagram_video gives up immediately rather than erroring.
    apify_api_key: str = ""
    # Paid Apify lookups one sync may queue. The rest wait for a later sync, so a Fivetran
    # change that makes hundreds of old creatives look like videos cannot spend in one go.
    apify_lookups_per_sync: int = 20

    worker_poll_seconds: float = 1.0
    # 'done' jobs are deleted after this many days; 'dead' rows are kept (queue.is_dead).
    job_retention_days: int = 30
    job_lease_seconds: int = 600

    cors_origins: str = "http://localhost:3000"

    @property
    def pool_url(self) -> str:
        return self.database_pool_url or self.database_url

    @property
    def account_read_grouping_dims_list(self) -> list[str]:
        return [d.strip() for d in self.account_read_grouping_dims.split(",") if d.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
