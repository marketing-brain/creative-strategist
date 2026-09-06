import { NextResponse } from 'next/server';
// Route handlers are server-only and are never part of any client bundle's
// module graph, so the Clerk-aware Node implementation can be imported
// directly here. (`src/lib/api.ts` cannot: it is reachable from 'use client'
// components, which is why it imports the isomorphic `./auth-headers` and
// relies on the `issuerLayer: 'rsc'` alias in next.config.ts instead.)
import { authHeaders } from '@/lib/auth-headers.server';

// Authenticated, per-org bytes: never prerender or cache this route.
export const dynamic = 'force-dynamic';

const base = () => (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

// Same-origin proxy for `GET /blobs/{key}` on the API. The browser requests
// this path from the Next server with no headers of its own (exactly what an
// <img>/<video> tag sends), and we re-issue it upstream with auth attached.
export async function GET(_req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  // Next decodes each dynamic-route segment individually, before this handler
  // ever sees it — so a single raw segment can be a percent-encoded string
  // that, once decoded, itself contains '/' or '\' and its own traversal
  // sequences (e.g. `..%2F..%2Fme` decodes to `../../me`). The `s === '..'`
  // check alone only catches a segment that IS exactly '..', not one that
  // CONTAINS a separator after decoding, so also reject any decoded segment
  // holding a raw path separator: a legitimate blob-key segment never has one.
  if (
    !path?.length ||
    path.some((s) => s === '' || s === '.' || s === '..' || s.includes('/') || s.includes('\\'))
  ) {
    return new NextResponse('invalid blob key', { status: 400 });
  }
  // Defense in depth: validate the final resolved URL rather than trusting
  // segment-level filtering to enumerate every encoding trick. The URL
  // constructor normalizes '..' (and, for the http/https special schemes,
  // treats '\' as '/'), so anything that still escapes `/blobs/` here is
  // rejected regardless of how it was encoded.
  const upstreamUrl = new URL(`/blobs/${path.join('/')}`, `${base()}/`);
  if (!upstreamUrl.pathname.startsWith('/blobs/')) {
    return new NextResponse('invalid blob key', { status: 400 });
  }
  const upstream = await fetch(upstreamUrl, {
    headers: await authHeaders(),
    cache: 'no-store',
  });
  if (!upstream.ok) return new NextResponse(null, { status: upstream.status });
  return new NextResponse(await upstream.arrayBuffer(), {
    status: upstream.status,
    headers: {
      'Content-Type': upstream.headers.get('content-type') ?? 'application/octet-stream',
      'Cache-Control': 'private, no-store',
    },
  });
}
