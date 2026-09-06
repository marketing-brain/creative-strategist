import { redirect } from 'next/navigation';
import { api } from '@/lib/api';

export default async function Home() {
  const accounts = await api.accounts();
  redirect(accounts.some((a) => a.status === 'active') ? '/creatives' : '/onboarding');
}
