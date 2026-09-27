import { redirect } from 'next/navigation';
import { api } from '@/lib/api';

/** The Lab is for admins. Anyone else lands on Ads instead of a page they cannot use. */
export async function requireAdmin(): Promise<void> {
  const me = await api.me().catch(() => null);
  if (me?.role !== 'admin') redirect('/creatives');
}
