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
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const token = await (window as any).Clerk?.session?.getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
