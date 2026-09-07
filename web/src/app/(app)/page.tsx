import { redirect } from 'next/navigation';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export default async function Home() {
  await requireAuth();
  const accounts = await api.accounts();
  redirect(accounts.some((a) => a.status === 'active') ? '/creatives' : '/onboarding');
}
