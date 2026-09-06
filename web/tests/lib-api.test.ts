import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/auth-headers', () => ({ authHeaders: async () => ({ 'X-Dev-User': 'u', 'X-Dev-Org': 'o', 'X-Dev-Role': 'admin' }) }));

describe('apiFetch', () => {
  beforeEach(() => { vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://api'); });

  it('prefixes url, merges headers, parses json', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('@/lib/api');
    const out = await apiFetch<{ ok: boolean }>('/health', { method: 'POST', body: JSON.stringify({ a: 1 }) });
    expect(out).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api/health');
    expect((init.headers as Record<string, string>)['X-Dev-Org']).toBe('o');
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });

  it('throws ApiError on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'nope' }), { status: 409 })));
    const { apiFetch, ApiError } = await import('@/lib/api');
    await expect(apiFetch('/x')).rejects.toBeInstanceOf(ApiError);
  });
});
