import { afterEach, describe, expect, it, vi } from 'vitest';

describe('authHeaders', () => {
  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (window as any).Clerk;
  });

  it('returns an Authorization header from the active Clerk session', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).Clerk = { session: { getToken: vi.fn().mockResolvedValue('jwt-token') } };
    const { authHeaders } = await import('@/lib/auth-headers');
    expect(await authHeaders()).toEqual({ Authorization: 'Bearer jwt-token' });
  });

  it('returns no headers when there is no active session', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).Clerk = { session: undefined };
    const { authHeaders } = await import('@/lib/auth-headers');
    expect(await authHeaders()).toEqual({});
  });
});
