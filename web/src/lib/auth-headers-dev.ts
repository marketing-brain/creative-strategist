// Shared, environment-agnostic helpers for auth-headers.ts / auth-headers.server.ts.
// Deliberately contains no reference to any Clerk package so it is safe to import
// from both the client and server variants (and therefore from client components).
export function isClerkMode(): boolean {
  return process.env.NEXT_PUBLIC_AUTH_MODE === 'clerk';
}

export function devAuthHeaders(): Record<string, string> {
  return {
    'X-Dev-User': process.env.NEXT_PUBLIC_DEV_USER ?? 'user_1',
    'X-Dev-Org': process.env.NEXT_PUBLIC_DEV_ORG ?? 'org_dev',
    'X-Dev-Role': process.env.NEXT_PUBLIC_DEV_ROLE ?? 'admin',
  };
}
