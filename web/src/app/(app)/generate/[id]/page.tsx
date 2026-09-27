import { redirect } from 'next/navigation';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export default async function GenerationRedirect({ params }: { params: Promise<{ id: string }> }) {
  await requireAuth();
  const { id } = await params;
  const g = await api.generation(id).catch(() => null);
  redirect(g ? `/make/${g.footage_id}` : '/make');
}
