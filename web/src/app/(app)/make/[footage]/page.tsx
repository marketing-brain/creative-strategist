import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { LiveRefresh } from '@/components/live-refresh';
import { Workspace } from '@/components/make/workspace';
import { api, ApiError } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Make' };

export default async function MakeVideoPage({
  params,
  searchParams,
}: {
  params: Promise<{ footage: string }>;
  searchParams: Promise<{ finding?: string }>;
}) {
  await requireAuth();
  const { footage } = await params;
  const { finding: findingId } = await searchParams;
  const v = await api.makeVideo(footage, findingId).catch((e) => {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  });
  const running =
    (v.brief && (v.brief.status === 'queued' || v.brief.status === 'running')) ||
    Object.values(v.brief?.renders ?? {}).some((r) => r.status === 'queued' || r.status === 'running') ||
    v.generations.some((g) => g.status === 'queued' || g.status === 'running');

  return (
    <div className="max-w-6xl space-y-6">
      <LiveRefresh active={!!running} everyMs={6000} />
      <Link href="/make" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden="true" /> Make
      </Link>
      {/* Keyed on the brief's version so a save or a finished job resets the editor's state. */}
      <Workspace key={`${v.brief?.id ?? 'none'}:${v.brief?.version ?? 0}:${v.brief?.status ?? ''}`} v={v} />
    </div>
  );
}
