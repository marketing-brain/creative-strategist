# Creative Strategist — Implementation Plans

Spec: `docs/superpowers/specs/2026-09-04-creative-strategist-design.md`

The thin end-to-end loop is built in six phases. Each phase is its own plan and
ends with working, tested software. Execute them in order; each plan's
**Interfaces → Consumes** block names exactly what it relies on from earlier phases.

| Phase | Plan | Delivers |
|---|---|---|
| 0 | `2026-09-04-phase-0-foundation.md` | Repo scaffold, config, Postgres + migrations, blob store, job queue, worker, scheduler, auth, API skeleton, docker compose, CI |
| 1 | `2026-09-04-phase-1-meta-ingest.md` | Meta OAuth, account selection, backfill and nightly sync of creatives, assets, and daily metrics |
| 2 | `2026-09-04-phase-2-tagging.md` | Taxonomy, image and video tagging with Claude, batches, embeddings, evals |
| 3 | `2026-09-04-phase-3-insights.md` | Metric views, pattern and fatigue insights, explanations, weekly brief, Copilot |
| 4 | `2026-09-04-phase-4-generation-publish.md` | Brand kit, copy, static and video generation, review queue, publish to Meta, loop closure |
| 5 | `2026-09-04-phase-5-web.md` | Next.js app: onboarding, creatives, insights, fatigue, copilot, generate, review, settings |

Shared conventions for every phase:

- Python 3.12, `uv`, FastAPI, asyncpg, raw SQL migrations via `dbmate`, pytest with `pytest-asyncio`.
- Every table has `org_id`; every repository function takes `org_id` as its first argument after `db`.
- IDs are UUIDv7 from `cs.core.ids.new_id()`; timestamps are `timestamptz`; enums are `TEXT` with `CHECK`.
- Blobs go through `cs.core.blobs.BlobStore`; the DB stores `path` + `sha256` only.
- Long work runs as a job in the `job` table via `cs.core.queue.enqueue`; handlers register with `@cs.jobs.registry.handler("job_type")`.
- External services sit behind a Protocol with a `Fake*` implementation under `src/cs/**/fakes.py`; tests use the fakes.
- Commit after every task with a conventional-commit message.

## Conventions added by the Phase 0 final review (binding for Phases 1–5)

- `db/migrations/20260904000002_schedule_state.sql` exists (scheduler persistence); later migrations are numbered from `000003` (ingest), `000004` (tagging), `000005` (tag_batch), `000006` (insights), `000007` (generation).
- Blob keys always start with `f"{org_id}/"`; `GET /blobs/{key}` requires auth and serves only the caller's org prefix, local backend only.
- `cs.core.queue`: `complete(db, job_id, worker_id)`, `fail(db, job_id, worker_id, error)`, `heartbeat(db, job_id, worker_id)`; `enqueue` stamps `schema_version: 1` into payloads; `reclaim_stale` dead-letters jobs already at `max_attempts`. Handlers run under a heartbeat, so long jobs are safe as long as the process is alive.
- The JSON codec encodes `UUID`, `datetime`/`date`, and `Decimal` automatically; job payloads may contain UUIDs directly.
- `AUTH_MODE` defaults to `clerk` and requires `CLERK_JWKS_URL`; `.env.example` and the test fixtures set `dev` explicitly.
- Scheduler last-run times persist in `schedule_state`; schedules registered in later phases should still use period-scoped dedup keys where a duplicate run would be costly.
- Local blob URLs (`/blobs/{key}`) require an auth header, so Phase 5 must not use them as bare `<img src>`; fetch bytes through the authenticated API client, or use S3 presigned URLs in non-dev environments.

## Conventions and follow-ups added by the Phase 1 final review (binding for Phases 2–5)

- No secret ever travels in a URL: `GraphClient` sends the Meta token as `Authorization: Bearer`, OAuth exchanges POST the app secret in the form body, `cs.core.logging.configure_logging()` (called by every entrypoint) keeps `httpx`/`httpcore` at WARNING, and `cs.connectors.meta.scrub.strip_access_tokens` scrubs raw pages before they reach blob storage. Phase 4 uploads/publishes must keep this invariant.
- Repository additions: `repo.upsert_metrics` returns `(written, skipped)`; `Database.executemany` exists for chunked writes; `ensure_metric_partition` tolerates concurrent creation.
- `service.sync_account` returns `{ads, new_creatives, failed_creatives, metrics, metrics_skipped}` and the handlers log it. Per-creative failures never abort an account sync (Global Constraint); one bad creative is logged, counted, and retried on the next sync.
- Spec deviations ruled during Phase 1 (see the branch's SDD ledger rulings, reproduced in the Phase 1 session summary): no 200-ad sub-job chunking for backfill; rate limits back off in-process (capped at 900s + jitter) under the job heartbeat instead of rescheduling the job; connector method names follow the plan (`list_ads`/`get_creative`/`upload_image`/`upload_video`), not the spec's `list_creatives`/`upload_asset`; derived CTR/CPA/ROAS are computed in `routers/creatives.py`, not a SQL view.
- Known limitations to pick up in later phases:
  - Carousel creatives get the `carousel` format label but no asset download and therefore no tagging (child attachments are not walked). Phase 2 should either add child-image downloads with `asset.seq` or exclude carousels explicitly.
  - Every `tagging.tag_creative` job enqueued by Phase 1 has no handler yet and dead-letters after 5 attempts. Phase 2 must open with a "re-enqueue tagging for creatives that have an original asset and no tag_run" step.
  - Monthly `metric_daily` partitions are created lazily in the sync hot path; a scheduled pre-creation job (current + next month) is a Phase 3 candidate.
  - `POST /accounts/{id}/activate` re-enqueues a full backfill even for an already-active account.
  - Phase 3 should introduce the derived-metrics SQL view spec §4 describes so insights and the API share one definition.
  - `scripts/record_meta_fixtures.py` has not been run (no Meta token on this machine); run it once with `META_ACCESS_TOKEN`/`META_ACCOUNT_ID` set and commit the `tests/fixtures/meta/recorded_*.json` files to activate the parametrized parse test.

## Execution status

- Phase 0: complete, merged to `main` on 2026-09-04 (54 tests). Executed with `superpowers:subagent-driven-development`.
- Phase 1: implemented on branch `feat/phase-1-meta-ingest` on 2026-09-05 (98 tests, 1 expected skip). Executed with `superpowers:subagent-driven-development`; six tasks, one final-review fix wave. Merge to `main` when the final re-review is clean.
- Phase 2: not started. Start it with: "Use superpowers:subagent-driven-development to execute docs/superpowers/plans/2026-09-04-phase-2-tagging.md on a new branch from main."

## Local environment notes (this machine)

- Docker is not installed. Postgres 16.2 + pgvector 0.6.2 runs from the `pgserver` wheel's bundled binaries: data dir `~/.local/share/cs-pg16`, listening on 127.0.0.1:5432, user `cs`, trust auth, databases `cs` and `cs_test`.
  Start: `.venv/lib/python3.12/site-packages/pgserver/pginstall/bin/pg_ctl -D ~/.local/share/cs-pg16 -o "-p 5432 -k /tmp -c listen_addresses=127.0.0.1" -l ~/.local/share/cs-pg16.log start`
  Check: `pg_isready -h 127.0.0.1 -p 5432`
- `dbmate` binary is at `~/.local/bin/dbmate` (tests resolve it via `shutil.which` or that path).
- A Homebrew install of `postgresql@16 pgvector dbmate` finished on 2026-09-05 (it took hours on this macOS). It is not started; the pgserver instance above remains the one in use. Do not run both on 5432.
- `.env` exists (gitignored) with `AUTH_MODE=dev` and 127.0.0.1 database URLs. Meta app credentials (`META_APP_ID`, `META_APP_SECRET`) are not set yet; Phase 1 tests do not need them.
