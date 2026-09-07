# WorkOS Auth — Replace Clerk Design

**Date:** 2026-09-06
**Status:** Approved for planning
**Scope:** Replace Clerk with WorkOS AuthKit as the app's identity provider. Adds a real login page and auth enforcement (neither exists today — `ClerkProvider`/`clerkMiddleware()` are wired up but nothing currently requires a session or redirects an unauthenticated user anywhere). Supersedes the original design's "Auth: Managed provider (Clerk)" decision and its "SSO" non-goal.

## 1. Goals and non-goals

### Goals

1. Every non-`dev` request must be authenticated by WorkOS before it can reach the app or the API.
2. A user who isn't signed in is redirected to a real login page (WorkOS's hosted AuthKit sign-in) and returned to the app on success.
3. The existing per-org data isolation (every table and query already scoped by `org_id`) continues unchanged — it was already the complete answer to "the view should be different for every organization," so nothing new is needed there.
4. `AUTH_MODE=dev` (the local `X-Dev-*` header bypass) is untouched.

### Non-goals

- Per-org branding/theming.
- Self-serve organization signup — organizations stay admin-provisioned via the WorkOS dashboard, users invited into them, matching how the local dev org (`org_dev`) is provisioned today.
- Any change to authorization logic beyond the `admin`/`member` role distinction that already exists.

## 2. Decisions taken

| Decision | Choice |
|---|---|
| Provider | WorkOS, replacing Clerk entirely (not run alongside it) |
| Login UI | WorkOS AuthKit's hosted sign-in page via `@workos-inc/authkit-nextjs` — no custom `/login` page to build |
| Org provisioning | Admin-provisioned via WorkOS dashboard; no self-serve org creation |
| Session claim mapping | Same `AuthContext { user_id, org_id, role }` contract as today; only the extraction function's internals change |
| DB schema | `org.clerk_org_id` renamed to `org.external_org_id` (a new migration; the column held one row at design time) |

## 3. Auth flow

```
Browser → GET /creatives (unauthenticated)
        → authkitMiddleware redirects to WorkOS-hosted sign-in
        → user authenticates (WorkOS handles it entirely)
        → WorkOS redirects to our /callback?code=...
        → callback exchanges code for a session (encrypted cookie)
        → subsequent requests: middleware refreshes the session transparently
        → server components/route handlers call getAccessToken() to get
          a WorkOS-issued JWT, attach it as Bearer to API calls
        → FastAPI verifies the JWT against WorkOS's JWKS, extracts
          org_id + role, maps to the existing AuthContext exactly as
          Clerk does today (org-scoped data logic is untouched)
```

## 4. Backend changes

- **New migration** (`db/migrations/`): `ALTER TABLE org RENAME COLUMN clerk_org_id TO external_org_id;`. Applies to both Supabase databases (`cs`, `cs_test`) and CI's ephemeral Postgres service automatically.
- **`cs/core/config.py`**: `auth_mode: Literal["dev", "workos"]`. Replace `clerk_jwks_url`/`clerk_issuer` with `workos_client_id` (WorkOS derives the JWKS URL from the client ID) and `workos_api_key`. Update `_check_clerk_config` (rename to `_check_workos_config`) to require `workos_client_id` when `auth_mode == "workos"`.
- **`cs/api/auth.py`**: rename `_clerk_claims` → `_workos_claims`. Same responsibilities — extract `sub`, an org identifier, and a role from the verified JWT — but the exact claim names/shapes must be confirmed against a real WorkOS access token during implementation rather than assumed from Clerk's shape (Clerk uses `org_role` with an `org:` prefix; WorkOS's RBAC claims are structured differently and this needs verifying, not guessing). `AuthContext`, `current_auth`, and `require_admin` keep their existing signatures — no downstream router or org-scoped query changes.
- **`cs/orgs/repo.py`**: `get_or_create_org` queries `external_org_id` instead of `clerk_org_id`.

## 5. Frontend changes

- **`package.json`**: remove `@clerk/nextjs`, add `@workos-inc/authkit-nextjs`.
- **`src/middleware.ts`**: `authkitMiddleware()` replaces `clerkMiddleware()`, configured to redirect unauthenticated requests to the hosted sign-in page.
- **`src/app/layout.tsx`**: `<AuthKitProvider>` replaces `<ClerkProvider>`.
- **`src/app/callback/route.ts`** (new): WorkOS's `handleAuth()` helper exchanges the redirect's `code` for a session cookie.
- **`src/lib/auth-headers.server.ts`**: swap Clerk's `auth().getToken()` for AuthKit's equivalent (`withAuth()`/`getAccessToken()`) — same call sites, same contract, so nothing that calls `authHeaders()` needs to change.
- **Env vars**: drop `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`/`CLERK_SECRET_KEY`; add `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, `WORKOS_COOKIE_PASSWORD` (session-cookie encryption key), `WORKOS_REDIRECT_URI`.

## 6. Error handling

- **JWKS/provider unreachable**: `_workos_claims` catches lookup failures, raises `503 "auth provider unavailable"` — unchanged from today.
- **Invalid/expired token**: `401` — unchanged.
- **Verified token with no org claim**: `403 "no active organization"` — unchanged. This is the practical enforcement of "admin-provisioned orgs only": a WorkOS user with no organization membership yet cannot get past this check anywhere in the API.
- **Frontend session expiry**: AuthKit middleware refreshes transparently; on refresh failure it redirects to the hosted sign-in page rather than leaving the app in a broken state.
- **Callback failure** (user cancels on WorkOS's page, code exchange fails): redirect to `/` rather than a raw error page, matching the app's existing quiet error-boundary style (`web/src/app/error.tsx`).

## 7. Testing

- `tests/api/test_clerk_auth.py` → `tests/api/test_workos_auth.py`: same test shapes (valid admin, member role, missing org, bad signature, missing sub, expired token, missing bearer header, JWKS failure, `require_admin` rejection) with WorkOS-shaped claims once confirmed.
- `tests/core/test_config.py`: rename clerk-mode tests to workos-mode, same assertion structure.
- `web/tests/blobs-route.test.ts`: comment-wording update only — the mock already targets `authHeaders()`, not Clerk directly.
- No changes needed to any test of org-scoped data access; that logic never touches the auth provider.

## 8. Explicitly out of scope

- Per-org branding/theming.
- Self-serve org creation/signup UI.
- Any change to `AUTH_MODE=dev`.
