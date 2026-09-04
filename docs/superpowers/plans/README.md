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
