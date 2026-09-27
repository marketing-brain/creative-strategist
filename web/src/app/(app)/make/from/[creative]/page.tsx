import { redirect } from 'next/navigation';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

// "Make new versions" on an ad opens the Make page of the video behind it.
export default async function MakeFromAd({ params }: { params: Promise<{ creative: string }> }) {
  await requireAuth();
  const { creative } = await params;
  const r = await api.makeFromCreative(creative).catch(() => null);
  redirect(r ? `/make/${r.footage_id}` : '/make');
}
