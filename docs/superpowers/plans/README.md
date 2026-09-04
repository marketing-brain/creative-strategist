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
