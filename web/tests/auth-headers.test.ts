import { describe, expect, it, vi } from 'vitest';

describe('authHeaders', () => {
  it('returns dev headers in dev mode', async () => {
    vi.stubEnv('NEXT_PUBLIC_AUTH_MODE', 'dev');
    vi.stubEnv('NEXT_PUBLIC_DEV_USER', 'u');
    vi.stubEnv('NEXT_PUBLIC_DEV_ORG', 'o');
    vi.stubEnv('NEXT_PUBLIC_DEV_ROLE', 'member');
    const { authHeaders } = await import('@/lib/auth-headers');
    expect(await authHeaders()).toEqual({ 'X-Dev-User': 'u', 'X-Dev-Org': 'o', 'X-Dev-Role': 'member' });
  });
});
