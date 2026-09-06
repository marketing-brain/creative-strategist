import { auth } from '@clerk/nextjs/server';
import { devAuthHeaders, isClerkMode } from './auth-headers-dev';

// Server (Node) implementation of the isomorphic `authHeaders()` contract.
// next.config.ts aliases `@/lib/auth-headers` to this file for the server
// webpack compilation only, so it is never part of the client bundle's module
// graph and therefore never trips Next's client/server boundary check.
//
// Using a plain static import here (rather than a `webpackIgnore`d dynamic
// import) lets webpack bundle '@clerk/nextjs/server' itself for the server
// build, so it resolves the package's internal extensionless ESM imports
// (e.g. `./routeMatcher`) with webpack's lenient resolver at build time,
// instead of deferring to Node's native ESM resolver at runtime (which
// requires fully-specified specifiers and fails with ERR_MODULE_NOT_FOUND).
export async function authHeaders(): Promise<Record<string, string>> {
  if (!isClerkMode()) return devAuthHeaders();
  const token = await (await auth()).getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
