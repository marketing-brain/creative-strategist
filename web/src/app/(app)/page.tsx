import { redirect } from 'next/navigation';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export default async function Home() {
  await requireAuth();
  const accounts = await api.accounts();
  // The library is the front door, and `/next` is a destination you arrive at: the nav reads
  // creatives -> insights -> next, evidence before interpretation before recommendation, and
  // a menu that argues one shape while the app lands you somewhere else is lying about the
  // product. Landing here also costs one fewer API call than probing for a plan first.
  redirect(accounts.some((a) => a.status === 'active') ? '/creatives' : '/onboarding');
}
