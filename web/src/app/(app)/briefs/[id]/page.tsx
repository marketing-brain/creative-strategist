import { redirect } from 'next/navigation';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export default async function BriefRedirect({ params }: { params: Promise<{ id: string }> }) {
  await requireAuth();
  const { id } = await params;
  const b = await api.brief(id).catch(() => null);
  redirect(b ? `/make/${b.footage_id}` : '/make');
}
