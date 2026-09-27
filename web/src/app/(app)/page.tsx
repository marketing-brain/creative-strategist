import { redirect } from 'next/navigation';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export default async function Home() {
  await requireAuth();
  const accounts = await api.accounts();
  // Ads is the front door: the first step of the loop (This week was removed 2026-09-26).
  redirect(accounts.some((a) => a.status === 'active') ? '/creatives' : '/onboarding');
}
