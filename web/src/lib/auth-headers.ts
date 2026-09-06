import { devAuthHeaders, isClerkMode } from './auth-headers-dev';

// Client-safe implementation: this is the file api.ts's `./auth-headers` import
// resolves to for the browser bundle (and what vitest/tsc see by default). It
// must never import '@clerk/nextjs/server' (or anything that imports the
// `server-only` package) — Next.js's client/server boundary check flags that as
// reachable from a 'use client' module regardless of a runtime `typeof window`
// guard.
//
// The server-side (Node) implementation lives in `auth-headers.server.ts` and is
// swapped in for that import via a webpack alias in next.config.ts, scoped to
// Next's 'rsc' module layer, so this file is the only one ever reachable from
// client components' module graph.
export async function authHeaders(): Promise<Record<string, string>> {
  if (!isClerkMode()) return devAuthHeaders();
  // Canary for the alias above. If this browser-only file is ever loaded on the
  // server in clerk mode, `(window as any).Clerk` would quietly evaluate to
  // `undefined` and we would return {} — an unauthenticated request that looks
  // like "the user got logged out" rather than "the build config broke". Fail
  // loudly instead.
  if (typeof window === 'undefined') {
    throw new Error(
      'auth-headers.ts (browser-only) was loaded server-side in clerk mode: the ' +
        "next.config.ts webpack alias mapping '@/lib/auth-headers' to " +
        'auth-headers.server.ts for the rsc layer is no longer being applied ' +
        '(e.g. a Turbopack build, which ignores webpack() config). Import ' +
        "'@/lib/auth-headers.server' explicitly from server-only modules, or " +
        'port the alias to the active bundler.',
    );
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const token = await (window as any).Clerk?.session?.getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
