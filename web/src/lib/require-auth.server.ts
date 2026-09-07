import { auth } from '@clerk/nextjs/server';

// Clerk's middleware-based route matching (createRouteMatcher) is deprecated
// in favor of protecting each resource directly, so every protected page
// calls this at the top of its Server Component instead of middleware
// gating routes centrally. No-op outside clerk mode, so AUTH_MODE=dev is
// unaffected.
export async function requireAuth(): Promise<void> {
  if (process.env.NEXT_PUBLIC_AUTH_MODE === 'clerk') {
    await auth.protect();
  }
}
