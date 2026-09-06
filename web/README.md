# creative-strategist web

The Next.js (App Router) front end for creative-strategist: creative library and
detail pages, insights and fatigue, the weekly brief, the copilot chat, the
generation form, the review queue and the publish dialog.

It is a thin client over the FastAPI backend in `../src/cs` — every page is a
server component that calls the API through `src/lib/api.ts`. It has no database
or state of its own.

## Running it

    npm install
    cp .env.example .env.local     # NEXT_PUBLIC_API_URL, auth mode, dev identity
    npm run dev                    # http://localhost:3000

The backend must be running too (`make api` + `make worker` from the repo root).
See the root [README](../README.md) for the full local-setup story: Postgres and
MinIO, migrations, the worker/scheduler processes, and `make demo` for a
fake-connector mode that needs no real Meta credentials.

    npm test          # vitest (jsdom) unit + component tests
    npm run build     # production build / type + lint check
    npm run test:e2e  # Playwright; prefer `make web-e2e` from the repo root

## Layout

- `src/app/` — routes. Pages are async server components; `error.tsx` /
  `global-error.tsx` are the error boundaries.
- `src/app/blobs/[...path]/route.ts` — same-origin proxy for the API's
  `/blobs/{key}` route, which requires an auth header a bare `<img src>` cannot
  send. Use `blobUrl()` from `src/lib/blob-url.ts` on any asset URL the API
  returns before putting it in a `src` attribute.
- `src/components/` — client components (`'use client'`) plus the shadcn-style
  primitives under `src/components/ui/`.
- `src/lib/auth-headers*.ts` — the isomorphic auth-header contract. The browser
  file is `auth-headers.ts`; `auth-headers.server.ts` is swapped in for server
  components by a webpack alias in `next.config.ts`. Server-only modules should
  import `auth-headers.server` directly.
