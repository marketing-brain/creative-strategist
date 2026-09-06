import { clerkMiddleware } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';

export default process.env.NEXT_PUBLIC_AUTH_MODE === 'clerk' ? clerkMiddleware() : () => NextResponse.next();
// The first pattern skips `_next` and anything that looks like a static file
// (contains a dot). `/blobs/:path*` is listed explicitly because blob keys end
// in a file extension and would otherwise be skipped — the blob proxy route
// calls Clerk's `auth()`, which requires clerkMiddleware to have run.
export const config = { matcher: ['/((?!_next|.*\\..*).*)', '/blobs/:path*'] };
