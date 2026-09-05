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

## Connect a Meta account

Set `META_APP_ID`, `META_APP_SECRET` and `META_REDIRECT_URI` (default
`http://localhost:8000/meta/oauth/callback`) in `.env`, then:

1. `GET /meta/oauth/start` as an admin returns a `url`; open it and grant access.
2. Meta redirects back to `GET /meta/oauth/callback`, which exchanges the code for a
   long-lived token and stores every visible ad account with status `paused`.
3. `POST /accounts/{id}/activate` flips the account to `active` and enqueues the 180-day
   backfill (`make worker` runs it).
4. The scheduler (`make scheduler`) then re-syncs the trailing 3 days nightly.
