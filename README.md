# creative-strategist

AI creative strategist for paid social. See `docs/superpowers/specs/` for the design and `docs/superpowers/plans/` for the build plans.

## Local setup

    brew install uv dbmate ffmpeg          # macOS
    cp .env.example .env                    # fill TOKEN_ENCRYPTION_KEY at minimum
    make up                                 # postgres + minio
    make install migrate
    make test

## Processes

    make api        # http://localhost:8000
    make worker     # runs jobs from the `job` table
    make scheduler  # enqueues recurring jobs

## Web app

The Next.js front end lives in `web/` and talks to the API above.

    cd web && npm install && cd ..
    cp web/.env.example web/.env.local   # NEXT_PUBLIC_API_URL, auth mode, dev identity
    make web-dev                          # http://localhost:3000
    make web-test                         # vitest unit + component tests
    make web-e2e                          # seeds a fixture Fivetran schema, runs Playwright

To click through the app without real Meta or model credentials:

    make demo       # CS_FAKE_CONNECTOR=1: seeds a fixture Fivetran schema, then api + worker + scheduler
    make web-dev    # in a second terminal

`make demo` runs `scripts/seed_dev_fivetran.py`, which drops a fixture
`<schema>__meta_ads` schema (shaped like a real Fivetran-landed Meta Ads sync) for the
dev org and runs the real transform against it once, leaving a `paused` ad account ready
for onboarding's "Activate" button. `CS_FAKE_CONNECTOR=1` fakes the tagging pipeline
(tagger/transcriber/embedder), so onboarding, sync, the creative library, insights and
the brief all work offline.

### Known limitations

- `make web-e2e` cannot go fully green without real credentials: generation always needs a
  working `ANTHROPIC_API_KEY` / `AI_GATEWAY_API_KEY`, and tagging does too unless you run
  `make api`/`make worker` with `CS_FAKE_CONNECTOR=1`.
- With `BLOB_BACKEND=local`, `GET /blobs/{key}` requires an auth header. The web
  app serves assets through its own `/blobs/...` route, which re-issues the
  request server-side with auth attached; never point an `<img src>` straight at
  the API's blob URL. In S3 mode the API returns presigned URLs instead.

## Connect a Meta account

Set `FIVETRAN_API_KEY`, `FIVETRAN_API_SECRET` and `FIVETRAN_GROUP_ID` in `.env`, then:

1. `POST /connections/meta/start` as an admin creates a Fivetran connector and returns a
   `connect_card_url`; open it and grant access to a Meta ad account.
2. The Fivetran Connect Card redirects back to the web app's `/fivetran-return` page,
   which calls `POST /connections/meta/finalize` with the `connector_id`. That stores the
   `meta_connection` row and enqueues `meta.await_first_sync`, which polls Fivetran until
   its first sync completes.
3. Once Fivetran's first sync succeeds, `meta.sync_from_fivetran` runs automatically: it
   discovers every ad account into `ad_account` rows (status `paused`) and syncs their
   creatives, ads and insights from the landed schema.
4. `POST /accounts/{id}/activate` flips an account to `active` and re-enqueues
   `meta.sync_from_fivetran` for it (`make worker` runs it).
5. The scheduler (`make scheduler`) then re-syncs every `ready` connection with an active
   account nightly.
