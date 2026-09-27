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
    # Tagging gates (cs.tagging.gates). A file is tagged once it can answer a question:
    # the rate metrics need impressions, the retention curve needs plays, and both floors
    # match the ones the analysis applies (account_read.stats, insights.play_curve).
    tagging_min_impressions: int = 2000
    tagging_min_video_plays: int = 1000
    # Paid tag runs allowed per file. Raise it to measure tagger agreement deliberately
    # (cs.insights.account_read.reliability scores files tagged more than once).
    tagging_repeat_per_footage: int = 1
    # No cap on how many files the sweep queues: what is worth tagging is decided by the
    # gate above, and tagging is claimed only when no other job is waiting
    # (cs.core.queue.BACKGROUND_JOB_TYPES), so a long backlog delays nothing else.
    # Open-vocabulary merge (spec 2026-09-16-vocab-merge-design.md): slug embeddings for
    # candidate recall. pgvector HNSW caps at 2000 dims; 1536 keeps the index option open.
    embedding_model: str = "google/gemini-embedding-2"
    embedding_dimensions: int = 1536
    # The merge judge is text-only and cheap either way, but flash-lite over-merged in
    # dry run 1 (ingredient vs. branded form, falling asleep vs. sleep quality). A wrong
    # merge is destructive and only reversible by hand, so it runs on the full flash tier.
    judge_model: str = "google/gemini-2.5-flash"
    # The `/next` recut card. The writer phrases what the statistics found and may not emit a
    # number (narrate.render enforces it in code); the critic checks the card against the same
    # facts and is the reason a card can be killed, so it is not the cheaper model.
    # Measured 2026-09-23: gemini-3-flash spent ~6,900 output tokens deliberating over a
    # 120-word card and ~7,100 on a pass/kill verdict. The card is phrasing, not reasoning --
    # the statistics are already decided -- so both run on the lite model, the same choice
    # `account_read_fix_model` made for the same reason.
    recut_write_model: str = "google/gemini-3.1-flash-lite"
    recut_critic_model: str = "google/gemini-3.1-flash-lite"
    # Account read (spec 2026-09-17). Grouping dimensions for bounded interaction tests;
    # a grouping value needs account_read_min_units units before it is used as a group.
    account_read_grouping_dims: str = (
        "audience_callouts,products_mentioned,features_mentioned,messenger,duration_s"
    )
    account_read_min_units: int = 10
    # Comparisons inside groups ("within founder-led ads..."). They are 96% of the run's
    # work and of its tests, the read is told never to lead with them, and every one of them
    # raises the bar the account-wide results must clear: the false-discovery correction is
    # computed across the whole run, so 5,700 within-group tests make a real account-wide
    # result 50x harder to call strong. Off for the read; the ask box can compute a group
    # on demand.
    account_read_interactions: bool = False
    # Draws per permutation test. At 1,000 the finest p-value is 0.001 and ~1% of grades are
    # decided by which shuffles came up; at 10,000 it is 0.0001, and since the shuffles are
    # now shared across a run it costs seconds rather than minutes.
    account_read_permutation_draws: int = 10_000
    # The account read's writer. The hardest reasoning job here by a distance: 77k characters
    # of graded comparisons, a dozen rules about what may be claimed from them, and citations
    # that have to match. It stays on the newer tier even though every other job runs on 2.5,
    # because consistency is not worth a worse read.
    account_read_model: str = "google/gemini-3-flash"
    # Variant briefs (/briefs): one call per brief writes slots, edits, pickups and a script.
    brief_model: str = "google/gemini-3-flash"
    # Generate tab: video edits run on WaveSpeed (Seedance 2.5 edit is not on the AI Gateway).
    wavespeed_api_key: str = ""
    # Corrections run on a model that does not deliberate. Measured 2026-09-23 on the
    # gateway: gemini-3-flash spends ~96% of its output on reasoning whatever it is told,
    # so a correction cost 65,000 output tokens and five minutes to apply an edit the gates
    # had already named; gemini-3.1-flash-lite returns the same little JSON with none.
    account_read_fix_model: str = "google/gemini-3.1-flash-lite"
    # One draft can run for minutes on a 23k-token brief. The client waits this long and
    # never retries on its own; a dropped call may already have been billed.
    account_read_timeout_s: float = 900.0

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
