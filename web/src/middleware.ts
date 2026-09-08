import { clerkMiddleware } from '@clerk/nextjs/server';

export default clerkMiddleware();
// The first pattern skips `_next` and anything that looks like a static file
// (contains a dot). `/blobs/:path*` is listed explicitly because blob keys end
// in a file extension and would otherwise be skipped — the blob proxy route
// calls Clerk's `auth()`, which requires clerkMiddleware to have run.
export const config = { matcher: ['/((?!_next|.*\\..*).*)', '/blobs/:path*'] };
