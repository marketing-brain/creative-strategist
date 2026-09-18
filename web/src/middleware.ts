import { clerkMiddleware } from '@clerk/nextjs/server';

// CLERK_MIDDLEWARE_DEBUG=1 in the server env makes Clerk log, per request, why it treated
// the session as it did (token expired, handshake outcome, cookie state). Off by default.
export default clerkMiddleware({ debug: process.env.CLERK_MIDDLEWARE_DEBUG === '1' });
// The first pattern skips `_next` and anything that looks like a static file
// (contains a dot). `/blobs/:path*` is listed explicitly because blob keys end
// in a file extension and would otherwise be skipped — the blob proxy route
// calls Clerk's `auth()`, which requires clerkMiddleware to have run.
export const config = { matcher: ['/((?!_next|.*\\..*).*)', '/blobs/:path*'] };
