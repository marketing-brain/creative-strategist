# Creative Strategist — Thin End-to-End Loop Design

**Date:** 2026-09-04
**Status:** Approved for planning
**Scope:** First sub-project of a sellable, multi-tenant SaaS modeled on Crux (getcrux.ai): connect a Meta ad account, ingest creatives and metrics, tag every image and video creative, surface data-backed insights and fatigue alerts, generate on-brand image and video variants, and publish approved variants back to Meta.

## 1. Goals and non-goals

### Goals

1. Prove the full loop on one platform (Meta) with real account data: ingest → tag → insight → generate → approve → publish → measure.
2. Establish the data model and module boundaries that later sub-projects (Google/TikTok connectors, market research, billing) build on without restructuring.
3. Multi-tenant from day one: every row and every query is scoped by `org_id`.

### Non-goals (later sub-projects)

Google and TikTok connectors; competitor ad-library scraping and social listening; billing and plans; SSO; roles beyond `member` and `admin`; carousel generation; custom attribution or BI imports; template-based (Remotion) video.

## 2. Decisions taken

| Decision | Choice |
|---|---|
| Product shape | Sellable multi-tenant SaaS |
| Platform in v1 | Meta only, behind a connector interface |
| Creative formats | Images and video (tagging and generation) |
| Architecture | Modular monolith: one Python codebase, three processes (api, worker, scheduler) plus Next.js web |
| Auth | Managed provider (Clerk) for users, orgs, invites |
| Video generation | Hosted image-to-video model behind a `VideoGenerator` interface |
| Default LLM | `claude-opus-5`, configurable per stage; refusal fallback enabled |
| Queue | Postgres-backed job table; no Redis in v1 |
| Storage | Postgres 16 + pgvector; S3-compatible blob store (MinIO local, S3/R2 prod) |

## 3. System shape

```
                 ┌──────────────┐        ┌──────────────┐
  Browser ──────▶│  web (Next)  │──JWT──▶│  api (FastAPI)│──┐
                 └──────────────┘        └──────────────┘  │
                                                  │         │ enqueue
                     Meta OAuth / webhooks ───────┘         ▼
                                                   ┌──────────────┐
      scheduler ── enqueue nightly/hourly ────────▶│  job table   │
                                                   └──────────────┘
                                                          │ claim
                                                          ▼
                                                   ┌──────────────┐
    Meta API ◀──── connectors/meta ────────────────│   worker     │
    Claude   ◀──── tagging, insights, generation ──│ (N procs)    │
    Image/Video gen ◀── generation ────────────────│              │
                                                   └──────────────┘
                                                          │
                                        Postgres ◀────────┴────────▶ Blob store
```

**Processes**

- `api` — FastAPI. Verifies Clerk JWTs, maps user → org, serves org-scoped REST, handles Meta OAuth callback, enqueues jobs. Never does long work inline.
- `worker` — same codebase, claims jobs from the job table with `SELECT … FOR UPDATE SKIP LOCKED`. One process handles any job type; scale by running more processes. Video jobs run here with ffmpeg available in the image.
- `scheduler` — single loop that enqueues recurring jobs: nightly metric sync per active `ad_account`, hourly fatigue check per org, weekly brief per org. Idempotent via a `(job_type, dedup_key)` unique index on pending jobs.
- `web` — Next.js App Router with Clerk. Calls only `api`.

**Package layout** (`src/cs/`)

```
core/         config, db (asyncpg + raw SQL), blobs, queue, ids (uuid7), crypto (token encryption)
connectors/   base.py (AdPlatformConnector interface), meta/ (oauth, client, sync, publish)
tagging/      taxonomy.py (loads YAML), image.py, video.py (ffmpeg, transcription), runner.py, embed.py
insights/     patterns.py (SQL aggregations), fatigue.py, explain.py (LLM), brief.py, copilot.py
generation/   brand.py, copy.py, images.py (ImageGenerator iface + providers), video.py (VideoGenerator iface + providers), compose.py (Pillow/ffmpeg resize + logo)
publish/      meta.py (upload asset, create creative + ad, paused)
api/          routers per domain, auth dependency, schemas
jobs/         registry mapping job_type → handler
```

Rule: a module imports `core` and itself. Cross-module needs go through explicit service functions (e.g. `tagging.runner.enqueue_for_creative(creative_id)`), never through another module's tables directly. This keeps each module extractable into its own service later.

## 4. Data model

Conventions follow `../db-design.md`: singular table names, UUIDv7 app-generated ids, `timestamptz`, enums as `TEXT + CHECK`, JSONB payloads carry top-level `schema_version`, blobs never in the DB (store `*_path` + `*_sha256`), derived artifacts append-only with one-way status transitions, raw SQL migrations (dbmate).

### Mutable registries

- `org(id, name, clerk_org_id, active_brand_kit_id, created_at)`
- `user_org(user_id, org_id, role CHECK IN ('member','admin'))` — `user_id` is the Clerk user id.
- `ad_account(id, org_id, platform CHECK IN ('meta'), platform_account_id, name, currency, access_token_enc, token_expires_at, status, sync_cursor JSONB, created_at)`
- `brand_kit(id, org_id, version, colors JSONB, fonts JSONB, logo_path, logo_sha256, voice_rules TEXT, prohibited_claims TEXT[], created_at)` — new version = new row; `org.active_brand_kit_id` points at current.
- `taxonomy_version(id, version, yaml_sha256, created_at)`
- `job(id, org_id, job_type, dedup_key, payload JSONB, status CHECK IN ('pending','running','done','failed','dead'), attempts, max_attempts, run_after, locked_by, locked_at, error, created_at, finished_at)`

### Append-only lineage

- `creative(id, org_id, ad_account_id, platform_creative_id, format CHECK IN ('image','video','carousel'), headline, primary_text, description, cta_type, link_url, primary_asset_id, generated_asset_id NULL, raw_path, first_seen_at, last_seen_at, schema_version)` — one row per Meta ad creative. `raw_path` points at the stored API response.
- `ad(id, org_id, ad_account_id, platform_ad_id, creative_id, adset_id, campaign_id, name, status, first_seen_at, last_seen_at)` — one row per Meta ad. Metrics arrive per ad and map to creatives through it; many ads can share one creative.
- `asset(id, org_id, creative_id, kind CHECK IN ('original_image','original_video','keyframe','audio','transcript','thumbnail'), path, sha256, mime, width, height, duration_ms, seq, created_at)`
- `metric_daily(org_id, ad_account_id, creative_id, ad_id, day, placement, spend, impressions, clicks, link_clicks, conversions, conversion_value, video_3s_views, video_thruplay, video_p50, video_p100, raw JSONB)` — partitioned by month on `day`. Primary key `(creative_id, ad_id, day, placement)`. Derived metrics (CTR, CPA, ROAS, thumbstop, hold rate) are computed in views, not stored.
- `tag_run(id, org_id, creative_id, taxonomy_version_id, model, status, prompt_sha256, input_tokens, output_tokens, created_at, finished_at)` — unique on `(creative_id, taxonomy_version_id, model)`.
- `tag(id, org_id, tag_run_id, creative_id, dimension, value, confidence, evidence)` — index on `(org_id, dimension, value)`.
- `creative_embedding(creative_id, tag_run_id, embedding vector(1536))`
- `insight(id, org_id, ad_account_id, kind CHECK IN ('pattern','fatigue'), dimension, value, window_start, window_end, sample_creatives, sample_spend, metric, baseline, observed, lift, explanation, suggested_test, created_at)`
- `brief(id, org_id, week_start, content_md, insight_ids UUID[], created_at)`
- `generation(id, org_id, brand_kit_id, source_insight_id, source_creative_ids UUID[], concept TEXT, kind CHECK IN ('static','video'), model, prompt, status, created_by, created_at, finished_at)`
- `generated_asset(id, generation_id, org_id, kind CHECK IN ('image','video'), aspect CHECK IN ('1:1','4:5','9:16'), path, sha256, headline, primary_text, cta_type, review_state CHECK IN ('draft','approved','rejected'), reviewed_by, reviewed_at, created_at)` — `review_state` is the only mutable column and moves one way from `draft`.
- `publish_job(id, org_id, generated_asset_id, ad_account_id, campaign_id, adset_id, ad_status_requested, platform_creative_id, platform_ad_id, status, error, created_at, finished_at)`

Once a published ad appears in the next metric sync, a `creative` row is created for it with `generated_asset_id` set, closing the loop.

## 5. Meta ingest

- **Connect:** OAuth with scopes `ads_read`, `ads_management`, `business_management`. Exchange for a long-lived token; store encrypted (Fernet with a per-environment key). User selects ad accounts to sync from those the token can see.
- **Backfill job** (per ad account, chunked into sub-jobs of 200 ads): list ads → ad creatives → download image/video originals to `blobs/<org>/creatives/<creative_id>/` → store raw responses → insert `creative` + `asset` → enqueue `tag_creative`. Then pull `insights` with `time_increment=1`, `breakdowns=publisher_platform,platform_position`, trailing 180 days, into `metric_daily`.
- **Incremental job** (nightly): re-pull last 3 days of metrics (attribution windows shift), detect new creatives, enqueue tagging for them.
- **Rate limits:** read `X-Business-Use-Case-Usage` headers; per-account token bucket; back off and reschedule the job rather than fail.
- **Interface:** `AdPlatformConnector` with `list_creatives`, `download_asset`, `fetch_metrics`, `upload_asset`, `create_ad`. Meta is the only implementation in v1.

## 6. Tagging

- **Taxonomy:** `tagging/taxonomy.yaml`, versioned. ~40 dimensions in groups: hook (type, first-3s content), message angle, offer, CTA, visual style, people/faces, product presence and timing, text density, color palette, format, tone; video-only: pacing, music, voiceover, caption style. Each dimension declares allowed values or `free_text`. The YAML compiles to a JSON schema used for structured output.
- **Images:** one Claude call: image + taxonomy schema → validated JSON of `{dimension, value, confidence, evidence}`.
- **Video:** ffmpeg scene-change keyframes (max 24, plus frames at 0s, 1s, 3s), audio extracted and transcribed (behind a `Transcriber` interface; default is a whisper-class model through Vercel AI Gateway, with a direct provider as fallback); one Claude call with ordered frames, timestamps, and transcript.
- **Model:** default `claude-opus-5`, per-stage config. Adaptive thinking (default). Refusal fallback beta enabled. Structured outputs via `output_config.format`.
- **Batching:** backfills of >50 creatives go through the Message Batches API; incremental tagging is synchronous per job.
- **Idempotency:** `tag_run` unique on `(creative_id, taxonomy_version_id, model)`. Re-tagging on a new taxonomy version creates new runs; old runs stay.
- **Embeddings:** the tag set (dimension=value pairs plus copy) is embedded for nearest-neighbor search ("find creatives like this") and duplicate detection.
- **Evals:** a hand-labeled set of ~50 real creatives from the user's account checked into `tests/evals/`; agreement per dimension is tracked across taxonomy and prompt changes.

## 7. Insights and Copilot

- **Pattern insights** (nightly per ad account): for each `(dimension, value)` with ≥ N creatives and ≥ S spend in the window (defaults N=5, S=$1,000, window 30 days), compute spend-weighted CTR, CPA, ROAS, thumbstop, hold rate versus the account baseline. Surface where lift exceeds a threshold and sample size holds. Pure SQL over `metric_daily` ⋈ `tag`.
- **Fatigue** (hourly): for each creative live in the last 7 days, compare trailing 7-day CTR and CPA to its own first 14 days; flag when CTR drops >30% or CPA rises >30% with spend above a floor.
- **Explanation:** one Claude call per new insight writes a plain-language explanation and a suggested next test. Stored on the row.
- **Weekly brief:** Monday job assembles the week's insights and fatigue alerts into a Markdown brief.
- **Copilot:** chat over the account using Claude tool use. Tools are read-only query functions: `metrics_by_creative`, `tags_for_creative`, `top_creatives_by_dimension`, `insights`, `fatigue_alerts`, `similar_creatives`. No model-generated SQL in v1. Conversation history is stored per user.

## 8. Generation

- **Brand kit:** onboarding form: upload logo (SVG/PNG), pick colors, fonts, voice rules, prohibited claims. Versioned.
- **Inputs:** an insight, or a free-form concept, plus optional reference creatives. The generation job builds a brief from the insight's winning tags, the brand kit, and the references.
- **Copy:** Claude writes N headline / primary text / CTA sets; a second Claude pass checks each against voice rules and prohibited claims and rejects violations.
- **Statics:** `ImageGenerator` interface with `generate(prompt, reference_images, aspect) → bytes`. Default provider: image generation through Vercel AI Gateway (one credential, model swappable by config string); a direct-provider implementation is the fallback if a needed model is not on the gateway. Output post-processed by Pillow: resize/crop to 1:1, 4:5, 9:16; logo composited per brand kit placement rules.
- **Video:** `VideoGenerator` interface with `generate(still, prompt, duration_s) → bytes`. Default provider: video generation through Vercel AI Gateway. The gateway exposes video only through the TypeScript AI SDK, so the worker shells out to a small Node CLI in `tools/video-gen/` that wraps `experimental_startVideo` and `experimental_getVideoStatus`; the Python `VideoGenerator` interface hides this. Post-processing with ffmpeg: captions from the copy, logo end card, export per aspect.
- **Output:** `generated_asset` rows in `draft`. Generation failures are recorded per asset; a generation with some successes is `done` with partial results.

## 9. Publish

- Review queue lists `draft` assets grouped by generation. Approve/reject is a one-way transition recorded with reviewer and time.
- Approving opens a publish form: ad account, campaign, ad set (fetched live from Meta), editable copy, requested status (default `PAUSED`).
- `publish_meta` job: upload image/video → create ad creative → create ad → store platform ids on `publish_job`. Errors surface in the queue with a retry action.
- The next nightly sync ingests the published ad's metrics and creates its `creative` row linked to `generated_asset_id`.

## 10. Frontend

Next.js App Router, TypeScript, Tailwind, shadcn/ui, Clerk. Pages:

1. Onboarding: create org → connect Meta → pick accounts → brand kit.
2. Creatives: library with tag filters and metric columns; detail page with asset preview, tags with evidence, metric chart, similar creatives.
3. Insights: pattern cards with lift, sample size, explanation, "Generate from this" action; weekly brief.
4. Fatigue: alerts list with the creative and decay chart.
5. Copilot: chat with streaming responses.
6. Generate: form (insight or concept, references, kind, count) → generation status.
7. Review queue: approve/reject, publish form.
8. Settings: accounts, brand kit versions, members.

All API calls send the Clerk JWT. The api verifies it and resolves `org_id` from the active Clerk org.

## 11. Error handling, jobs, observability

- Job lifecycle `pending → running → done | failed`; `failed` re-queues with exponential backoff up to `max_attempts` (default 5), then `dead`. Stuck `running` jobs past a lease timeout are reclaimed.
- Failures are scoped to the smallest unit (one creative, one asset), never a whole account.
- External calls (Meta, Claude, transcription, image/video providers) each sit behind an interface with an in-memory fake used in tests.
- Structured logging with `org_id`, `job_id`, `creative_id`. Token and provider costs recorded per `tag_run` / `generation` for later billing.
- Secrets via environment variables; `.env.example` documents them.

## 12. Testing

- Unit tests per module (pytest, asyncio).
- Contract tests for the Meta connector against recorded fixtures under `tests/fixtures/meta/`, refreshed from the real account with a script.
- Tagging evals against the hand-labeled set; a CI check fails if per-dimension agreement drops below a floor.
- End-to-end test: fake Meta + fake Claude + fake generators, runs backfill → tag → insights → generate → approve → publish → sync, asserting the loop closes on a `creative` linked to a `generated_asset`.
- Frontend: component tests (Vitest + Testing Library) and one Playwright flow through onboarding → creatives → review.

## 13. Local development

`docker compose` with Postgres (pgvector image) and MinIO. `make dev` runs api, worker, scheduler, and web. `make migrate`, `make test`, `make evals`. Python managed with `uv`; Node with `npm`.
