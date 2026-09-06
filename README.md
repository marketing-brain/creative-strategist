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
    make web-e2e                          # seeds the dev account, runs Playwright

To click through the app without real Meta or model credentials:

    make demo       # CS_FAKE_CONNECTOR=1: api + worker + scheduler on fakes
    make web-dev    # in a second terminal

`CS_FAKE_CONNECTOR=1` fakes the Meta connector and the tagging pipeline, so
onboarding, sync, the creative library, insights and the brief all work offline.

### Known limitations

- `make web-e2e` cannot go fully green without real credentials. `CS_FAKE_CONNECTOR`
  does not fake generation, so the generate/review/publish leg of the flow needs a
  working `ANTHROPIC_API_KEY` / `AI_GATEWAY_API_KEY`.
- With `BLOB_BACKEND=local`, `GET /blobs/{key}` requires an auth header. The web
  app serves assets through its own `/blobs/...` route, which re-issues the
  request server-side with auth attached; never point an `<img src>` straight at
  the API's blob URL. In S3 mode the API returns presigned URLs instead.

## Connect a Meta account

Set `META_APP_ID`, `META_APP_SECRET` and `META_REDIRECT_URI` (default
`http://localhost:8000/meta/oauth/callback`) in `.env`, then:

1. `GET /meta/oauth/start` as an admin returns a `url`; open it and grant access.
2. Meta redirects back to `GET /meta/oauth/callback`, which exchanges the code for a
   long-lived token and stores every visible ad account with status `paused`.
3. `POST /accounts/{id}/activate` flips the account to `active` and enqueues the 180-day
   backfill (`make worker` runs it).
4. The scheduler (`make scheduler`) then re-syncs the trailing 3 days nightly.
