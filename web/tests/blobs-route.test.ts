import { beforeEach, describe, expect, it, vi } from 'vitest';

// Mock the Clerk-aware server auth so the route handler can be imported and
// exercised directly, without a real Clerk environment.
vi.mock('@/lib/auth-headers.server', () => ({
  authHeaders: async () => ({ Authorization: 'Bearer test-token' }),
}));

describe('GET /blobs/[...path]', () => {
  beforeEach(() => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://localhost:8000');
  });

  async function callWith(segments: string[]) {
    const { GET } = await import('@/app/blobs/[...path]/route');
    return GET(new Request('http://localhost:3000/blobs/x'), { params: Promise.resolve({ path: segments }) });
  }

  // The three payloads from the finding: each raw path segment, once Next
  // decodes it individually, contains its own '/' or '\' and traversal
  // sequences that resolve outside of /blobs/.
  const traversalPayloads: Array<{ name: string; segments: string[] }> = [
    { name: '..%2F..%2Fme', segments: ['org', '../../me'] },
    { name: '%2e%2e%2f%2e%2e%2fme', segments: ['org', '../../me'] },
    { name: '..%5C..%5Cme', segments: ['org', '..\\..\\me'] },
  ];

  it.each(traversalPayloads)('rejects the $name traversal payload with 400 and never calls fetch', async ({ segments }) => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await callWith(segments);

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects a single segment that decodes to an absolute path escape', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    // A segment containing a leading '/' after decoding, e.g. from
    // `%2Fme` — must not be treated as an absolute path.
    const res = await callWith(['/me']);

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects "." and ".." segments', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    expect((await callWith(['org', '..'])).status).toBe(400);
    expect((await callWith(['org', '.'])).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects an empty path', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await callWith([]);

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('still proxies a real, legitimate blob key with auth headers attached', async () => {
    const bytes = new TextEncoder().encode('fake-png-bytes').buffer;
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(bytes, { status: 200, headers: { 'content-type': 'image/png' } }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const res = await callWith(['org_id', 'sha256.png']);

    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('image/png');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBeInstanceOf(URL);
    expect((url as URL).toString()).toBe('http://localhost:8000/blobs/org_id/sha256.png');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer test-token');
  });
});
