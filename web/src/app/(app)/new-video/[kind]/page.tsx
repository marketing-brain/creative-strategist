import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { NewVideo } from '@/components/make/new-video';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'New video' };

// Make › New video for a kind: one kind of video's script, the video made from it, and its launch.
export default async function NewVideoPage({ params }: { params: Promise<{ kind: string }> }) {
  await requireAuth();
  const { kind } = await params;
  const cluster = decodeURIComponent(kind);
  const w = await api.whatWorks();
  const row = w.creator_scripts?.find((r) => r.cluster === cluster);
  if (!row) notFound();

  return (
    <div className="max-w-4xl space-y-6">
      <Link href="/new-video" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden="true" /> Make
      </Link>
      <header className="space-y-1.5">
        <p className="text-[13px] font-medium text-muted-foreground">New video for a kind</p>
        <h1 className="text-2xl font-semibold tracking-tight text-balance">{row.name}</h1>
        <p className="text-sm text-muted-foreground tabular-nums">
          {row.cpa != null ? `$${Math.round(row.cpa)} a purchase over the last 30 days` : 'No purchases in the last 30 days'} across {row.files}{' '}
          {row.files === 1 ? 'video' : 'videos'} of this kind.{' '}
          <Link href="/what-works" className="font-medium text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground">
            What holds up for it
          </Link>
        </p>
      </header>
      <NewVideo row={row} />
    </div>
  );
}
