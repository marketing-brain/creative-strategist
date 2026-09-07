# Migrate Meta Ingest to Fivetran; Drop Direct-Publish

**Date:** 2026-09-06
**Status:** Approved for planning
**Scope:** Replace the direct Meta OAuth + Graph API ingest pipeline with a Fivetran-sourced pipeline, and remove the direct-publish-to-Meta feature for this MVP in favor of a manual download flow.

## 1. Motivation

Connecting a Meta ad account today (`src/cs/connectors/meta/oauth.py`) uses a first-party Meta app requesting `ads_read`, `ads_management`, `business_management`. Those are Advanced Access permissions — while the app is in Development mode, only Meta accounts added as testers can authorize it, which blocks any org other than the owner's. Getting other orgs connected requires Meta App Review, which is slow and requires business verification.

Fivetran's `facebook_ads` connector runs under **Fivetran's own already-reviewed app**. Any org's Meta user can authorize it through a Fivetran Connect Card with no App Review on creative-strategist's side, for the read-only scopes Fivetran requests. This only covers *ingest* — Fivetran has no ad-creation API, so it can't carry the *publish* side (`upload_image`, `upload_video`, `create_ad`), which would still need App Review for `ads_management` regardless of this migration. Since publish is not required for this MVP, it is removed rather than kept behind a still-gated flow: **users download the generated asset and its copy, and publish it themselves in Ads Manager.**

## 2. Decisions taken

| Decision | Choice |
|---|---|
| Ingest source | Fivetran `facebook_ads` connector, replacing direct Meta OAuth + Graph API polling |
| Fivetran account | Reuse the existing Fivetran account (used by lp-console/Second Brain); new destination group scoped to this product |
| Fivetran destination | Postgres, pointed at creative-strategist's existing Supabase-hosted DB — **not** S3/Delta Lake (the AWS account backing lp_transform's bucket has been deleted, and Approach A would need new AWS infra anyway; Postgres is directly reachable and needs nothing new provisioned) |
| Destination isolation | A Fivetran-only Postgres role, scoped to `CREATE SCHEMA`/write only within schemas it owns (`fivetran_<org_slug>__meta_ads`), kept out of `public` and the app's Alembic/SQL migration chain |
| Connection pooling | Fivetran points at Supabase's session-mode pooler (port 5432), not the transaction-mode pooler (port 6543) — Fivetran issues DDL during sync, which transaction pooling does not support |
| Metric parity | A Fivetran **custom report** configured to match today's `INSIGHT_FIELDS` and `breakdowns=publisher_platform,platform_position` (Fivetran's default `basic_ad` report is thinner: impressions/inline_link_clicks/spend/reach/frequency only, no video metrics, no breakdowns) |
| Historical depth | Configured on the Fivetran connector itself (~180 days), replacing the app's own `BACKFILL_DAYS`/`INCREMENTAL_DAYS` windowing |
| Publish | Removed entirely for this MVP. Replaced with a download action (raw asset file + copyable ad copy fields) on an approved `generated_asset` |
| Existing data | None to preserve — all current `ad_account`/`creative`/`metric_daily` rows are local test data; clean cutover |

## 3. Architecture & data flow

```
                    ┌──────────────┐        ┌──────────────┐
   Browser ────────▶│  web (Next)  │──JWT──▶│  api (FastAPI)│
                    └──────────────┘        └──────────────┘
                                                    │
                                     create/verify connector (Fivetran REST, HTTP Basic)
                                                    ▼
                                            ┌───────────────┐
                                            │   Fivetran     │
                                            │ (facebook_ads  │◀── Meta OAuth consent
                                            │  connector)    │    (Fivetran's own app —
                                            └───────────────┘     no App Review needed)
                                                    │ writes raw tables
                                                    ▼
                              schema: fivetran_<org_slug>__meta_ads
                     ┌───────────────────────────────────────────────┐
                     │  campaign_history, ad_set_history, ad_history, │
                     │  creative_history, ad_image_history,           │
                     │  ad_video_history, asset_feed_spec_*,          │
                     │  <custom insights report>                      │
                     └───────────────────────────────────────────────┘
                                                    │ cross-schema SQL (same Postgres)
                                                    ▼
                                     job: meta.sync_from_fivetran
                        fold rows → PlatformAd / PlatformCreative / MetricRow
                        (same dataclasses base.py already defines)
                                                    │ repo.upsert_ad / upsert_creative / upsert_metrics
                                                    ▼
                          public.ad_account / ad / creative / asset / metric_daily
                                                    │ per-creative, missing-asset only
                                                    ▼
                          job: meta.download_creative / meta.download_carousel
                          (mirrors Meta CDN URL → blob storage, unchanged from today)
```

### Connect flow

1. Admin clicks **Connect Meta**. FastAPI (`POST /connections/meta/start`, `require_admin`) creates a Fivetran connector — `service=facebook_ads`, the shared Fivetran group, `schema=schema_for(org_id)` (`fivetran_<org_slug>__meta_ads`) — and returns `{connect_card_url, connector_id}`. Before creating, it looks up any existing connector for this org+provider's schema and deletes it first (an abandoned attempt or a reconnect), so retried connects are idempotent — same rule `lp-console`'s `connect.ts` uses.
2. The browser opens `connect_card_url` in a popup. The user authorizes via Meta, through Fivetran's app. Fivetran redirects the popup to a new `/fivetran-return` page, which `postMessage`s the opener and closes itself — ported directly from Second Brain's `app/fivetran-return/page.tsx`.
3. The opener calls `POST /connections/meta/finalize {connector_id}`. The backend fetches the connector from Fivetran, checks `connector.schema == schema_for(org_id)` (refusing to let an org claim a connector it doesn't own), and upserts `ad_account` with `fivetran_connector_id` set and `sync_status='pending'`.
4. Finalize enqueues `meta.await_first_sync` (see below), which transitions into `meta.sync_from_fivetran` once Fivetran's own first sync completes.

### Wait for Fivetran's first sync

Fivetran's own sync (pulling from Meta into the destination schema) takes time — the app can't run the transform against tables that don't exist yet. `meta.await_first_sync` polls `GET /connectors/{id}` (same call `Second Brain`'s `fivetran.get_connector` makes) and re-enqueues itself via `run_after` with backoff if the connector hasn't completed a first sync (`succeeded_at` unset or `sync_state == "syncing"`). `queue.enqueue`'s existing `run_after`/`max_attempts` mechanics cover this without a new primitive — no separate poller process needed. Once `succeeded_at` is present, it enqueues `meta.sync_from_fivetran` and flips `ad_account.status` from `paused` to `active`. If it exhausts `max_attempts` without success, `sync_status` is set to `error` with a message pointing at the Fivetran dashboard.

### Sync transform

`meta.sync_from_fivetran` (replacing `meta.backfill` and `meta.sync_incremental`) is a full per-org "replace with current truth" pass, run on connect and nightly thereafter — Fivetran itself owns re-sync cadence and historical depth, so the app no longer needs its own backfill/incremental windowing.

It reads, per org:
- **Entities** — `campaign_history`, `ad_set_history`, `ad_history`, `creative_history`, `ad_image_history`, `ad_video_history`, the `asset_feed_spec_*` tables (image/video/title/body/CTA/link variants) — all "history" tables that carry one row per Fivetran sync per id; the transform keeps the latest row per id (`row_number() OVER (PARTITION BY id ORDER BY _fivetran_synced DESC)`), the same pattern `lp_transform/transforms/meta_ads.py` already uses against these exact tables.
- **Insights** — the custom report table (see Decisions), folded into `MetricRow` per `(ad_id, day, placement)`.

Rows fold into the *existing* `PlatformAd`/`PlatformCreative`/`MetricRow` dataclasses (`src/cs/connectors/base.py`, unchanged), then persist through the *existing* `repo.upsert_ad`/`upsert_creative`/`upsert_metrics` (`src/cs/ingest/repo.py`, unchanged). Only the source of those dataclasses changes — this keeps `ad`/`creative`/`asset`/`metric_daily` and every downstream page (`creatives`, `insights`, `fatigue`, `review`) untouched.

For any creative missing a mirrored asset, the transform enqueues `meta.download_creative` (single image/video) or `meta.download_carousel` (unchanged handlers, just now triggered by the transform instead of the old Graph-API-driven `service.sync_account`) — preserving the existing fault-isolation property that one dead asset URL never fails the whole account's sync.

### Health

`ad_account` gains `sync_status`, `last_synced_at`, `last_error` (replacing `access_token_enc`, `token_expires_at`, which no longer apply — Fivetran holds the Meta token, not this app). `meta.sync_from_fivetran` writes these directly after each run; status mapping (`broken` → `error` / "Reconnect", `syncing` → `syncing`, else `ready`) is ported from Second Brain's `reconcile._map_state`. No separate reconciler process is needed — creative-strategist already runs its own job scheduler, so the sync job recording its own outcome is sufficient.

## 4. Component changes

**New:**
- `src/cs/connectors/fivetran/client.py` — REST client (`create_connector`, `get_connector`, `delete_connector`, `find_connector_by_schema`), HTTP Basic auth (`FIVETRAN_API_KEY`/`FIVETRAN_API_SECRET`). Python port of `lp-console/lib/fivetran.ts`.
- `src/cs/connectors/fivetran/connect.py` — `schema_for(org_id)`, `start_connection`, `finalize_connection`. Port of `lp-console/lib/connect.ts`, adapted to Clerk org ids and this app's `AuthContext`.
- `src/cs/api/routers/connections.py` — `POST /connections/meta/start`, `POST /connections/meta/finalize`, both `require_admin`. Replaces `src/cs/api/routers/meta_oauth.py`.
- `src/cs/ingest/fivetran_transform.py` — the entity + insights fold described above.
- Job handlers: `meta.await_first_sync`, `meta.sync_from_fivetran` in `src/cs/ingest/jobs.py`, replacing `meta.backfill`/`meta.sync_incremental`/`nightly_sync`'s Graph-API path (the nightly schedule now enqueues `meta.sync_from_fivetran` per active account instead).
- `web/src/app/fivetran-return/page.tsx` — popup-return page, port of Second Brain's.
- A **Download** action on an approved `generated_asset` (replacing `PublishDialog`): serves the raw asset file and surfaces the ad copy fields (headline, primary text, description, CTA, link) already shown in review, with a copy affordance. No new bundling/zip endpoint for MVP.

**Removed:**
- `src/cs/connectors/meta/oauth.py`, `src/cs/api/routers/meta_oauth.py`
- `src/cs/publish/` (`meta.py`, `jobs.py`), `POST /generated-assets/{id}/publish`, `GET /publish-jobs`
- `web/src/components/publish-dialog.tsx`, `api.publish`/`api.publishJobs`, `api.metaOauthStart`
- `upload_image`, `upload_video`, `create_ad` from `AdPlatformConnector` (`src/cs/connectors/base.py`), `MetaConnector` (`src/cs/connectors/meta/connector.py`), and `src/cs/connectors/fakes.py`
- `GraphClient`-driven ingest in `src/cs/ingest/service.py` (the read side — `MetaConnector.list_ads`/`get_creative`/`fetch_metrics`/`list_campaigns`/`list_adsets` and their Graph API calls are no longer invoked by ingest; kept or deleted per implementation-time judgment since nothing else calls them once publish is gone)
- `tests/connectors/test_meta_oauth.py`, `tests/api/test_meta_oauth_routes.py`, `tests/connectors/test_meta_publish.py`, `tests/publish/`, `tests/api/test_review_publish.py`

**Changed:**
- `ad_account`: drop `access_token_enc`, `token_expires_at`; add `fivetran_connector_id` (unique, not null), `sync_status`, `last_synced_at`, `last_error`.
- `web/src/components/accounts-panel.tsx`: **Connect Meta** calls the new start/finalize endpoints via a popup instead of `window.location.assign`; status badge reads `sync_status`.

## 5. Error handling

- Fivetran connector create/delete failures surface as today's 403/500s do in `accounts-panel.tsx`.
- Reconnect is idempotent: an existing connector for the org+provider schema is deleted before a new one is created.
- Finalize's schema-ownership check rejects a connector that isn't this org's.
- First-sync wait has bounded retries (existing `max_attempts` job semantics); exhausting it sets `sync_status='error'` with a message pointing at the Fivetran dashboard, rather than hanging silently.
- A broken connector (e.g. the user revokes Meta access from Facebook's side) is detected on the next `meta.sync_from_fivetran` run via the connector's `setup_state`, surfaced as `sync_status='error'` / "Reconnect" — same UX Second Brain's reconciler already established.
- Per-creative asset mirroring failures stay isolated to that creative's job, as today.

## 6. Known risk / first implementation task

Table and column shapes above are sourced from `lp_transform`'s working code (entity history tables — high confidence, it's live against a real connector) and Fivetran's public dbt-source docs (custom report shape — directionally correct, exact column names for a *custom* report with `publisher_platform`/`platform_position` breakdowns and video-action fields haven't been confirmed against a live connector). **First implementation task: stand up one real Fivetran `facebook_ads` connector with the custom report against a test ad account and inspect the landed schema before finalizing the insights transform SQL.**

## 7. Testing

- `src/cs/connectors/fivetran/client.py` — mocked via `respx`, same pattern as the existing `GraphClient` tests.
- `connect.py` — pure-function unit tests for `schema_for` and the ownership check.
- `fivetran_transform.py` — run against `DATABASE_URL_TEST`, with fixture tables seeded to mimic Fivetran's landed shape (no DuckDB/mocking needed now that it's plain Postgres).
- `connections.py` routes — adapted from the removed `test_meta_oauth_routes.py`.
