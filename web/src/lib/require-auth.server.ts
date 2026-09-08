import { auth } from '@clerk/nextjs/server';

// Every protected page calls this at the top of its Server Component instead
// of gating routes centrally in middleware, since Clerk's middleware-based
// route matching (createRouteMatcher) is deprecated in favor of protecting
// each resource directly.
export async function requireAuth(): Promise<void> {
  await auth.protect();
}
