# creative-strategist

AI creative strategist for paid social. See `docs/superpowers/specs/` for the design and `docs/superpowers/plans/` for the build plans.

## Local setup

    brew install uv dbmate ffmpeg          # macOS
    cp .env.example .env                    # fill ANTHROPIC_API_KEY for real tagging/insights/copilot
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
    cp web/.env.example web/.env.local   # NEXT_PUBLIC_API_URL + your Clerk keys
    make web-dev                          # http://localhost:3000
    make web-test                         # vitest unit + component tests
    make web-e2e                          # seeds a fixture Fivetran schema, runs Playwright

There is no dev-mode auth bypass and no fake-connector demo mode: every environment,
local development included, authenticates through Clerk and calls real Anthropic/AI
Gateway APIs. `scripts/seed_dev_fivetran.py` still seeds a fixture
`<schema>__meta_ads` schema (shaped like a real Fivetran-landed Meta Ads sync) so you
don't need a live Fivetran connector to exercise sync/activate locally, but tagging,
insights, briefs, and generation all need real `ANTHROPIC_API_KEY` /
`AI_GATEWAY_API_KEY` credentials to do anything.

### Known limitations

- `make web-e2e` needs a real, signed-in Clerk session to get past the app's login —
  Playwright can't click through it without either real test-user credentials or
  Clerk's Testing Tokens integration (`@clerk/testing`), neither of which is wired up
  yet. Until that's set up, `web/tests/e2e/onboarding-to-review.spec.ts` cannot run.
- With `BLOB_BACKEND=local`, `GET /blobs/{key}` requires an auth header. The web
  app serves assets through its own `/blobs/...` route, which re-issues the
  request server-side with auth attached; never point an `<img src>` straight at
  the API's blob URL. In S3 mode the API returns presigned URLs instead.
- Video ad creatives download their actual video file from `ad_video_history.source`
  (verified against a live connector) and get tagged normally. A video whose row hasn't
  landed yet, or whose `source` is empty (Meta still processing it), is skipped gracefully
  and picked up on a later sync -- it never fails the whole sync.
- Orgs that connected Meta before this Fivetran migration must reconnect: the migration
  adds `ad_account.meta_connection_id` without a backfill, so every pre-existing
  `ad_account` row starts with `meta_connection_id = NULL`. The old connection is not
  carried forward -- reconnect via the Connect Card flow below.

## Connect a Meta account

Set `FIVETRAN_API_KEY`, `FIVETRAN_API_SECRET` and `FIVETRAN_GROUP_ID` in `.env`, then:

1. `POST /connections/meta/start` as an admin creates a Fivetran connector and returns a
   `connect_card_url`; open it and grant access to a Meta ad account.
2. The Fivetran Connect Card redirects back to the web app's `/fivetran-return` page
   (opened in a popup). That page has no `connector_id` and does no auth of its own -- it
   just `postMessage`s a `fivetran:return` signal to the tab that opened it and closes
   itself. The opening tab's listener (in `accounts-panel.tsx`) receives that message and
   then calls `POST /connections/meta/finalize` with the `connector_id`, which stores the
   `meta_connection` row and enqueues `meta.await_first_sync`, which polls Fivetran until
   its first sync completes.
3. Once Fivetran's first sync succeeds, `meta.sync_from_fivetran` runs automatically: it
   discovers every ad account into `ad_account` rows (status `paused`) and syncs their
   creatives, ads and insights from the landed schema.
4. `POST /accounts/{id}/activate` flips an account to `active` and re-enqueues
   `meta.sync_from_fivetran` for it (`make worker` runs it).
5. The scheduler (`make scheduler`) then re-syncs every `ready` connection with an active
   account nightly.
