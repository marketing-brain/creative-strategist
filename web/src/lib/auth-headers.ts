export async function authHeaders(): Promise<Record<string, string>> {
  if (process.env.NEXT_PUBLIC_AUTH_MODE !== 'clerk') {
    return {
      'X-Dev-User': process.env.NEXT_PUBLIC_DEV_USER ?? 'user_1',
      'X-Dev-Org': process.env.NEXT_PUBLIC_DEV_ORG ?? 'org_dev',
      'X-Dev-Role': process.env.NEXT_PUBLIC_DEV_ROLE ?? 'admin',
    };
  }
  let token: string | null | undefined;
  if (typeof window === 'undefined') {
    const { auth } = await import(/* webpackIgnore: true */ '@clerk/nextjs/server');
    token = await (await auth()).getToken();
  } else {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    token = await (window as any).Clerk?.session?.getToken();
  }
  return token ? { Authorization: `Bearer ${token}` } : {};
}
