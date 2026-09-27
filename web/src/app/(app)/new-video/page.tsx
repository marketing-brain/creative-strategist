import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { KindStatus, MakeViews } from '@/components/make/make-views';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'New video' };

// Make › New video for a kind: every kind of video with its cost per purchase, cheapest first, and
// where its new video stands. Each opens /new-video/<kind>: the script, the video, the launch.
export default async function NewVideo() {
  await requireAuth();
  const w = await api.whatWorks().catch(() => null);
  const kinds = [...(w?.creator_scripts ?? [])].sort((a, b) => (a.cpa ?? 1e9) - (b.cpa ?? 1e9));

  return (
    <div className="max-w-6xl space-y-10">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Make</h1>
          <MakeViews current="new" />
        </div>
        <p className="max-w-prose text-sm text-muted-foreground">
          A brand-new video of a kind that works, from a script written with what holds up for it. Kinds with the lowest cost per
          purchase first. Launched videos move to Tests.
        </p>
      </div>
      <section className="space-y-3" aria-label="Kinds of video">
        {kinds.length === 0 && <p className="text-sm text-muted-foreground">No kinds of video have been read yet.</p>}
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border">
          {kinds.map((k) => (
            <li key={k.cluster}>
              <Link
                href={`/new-video/${encodeURIComponent(k.cluster)}`}
                className="group grid grid-cols-[minmax(0,1fr)_auto_1rem] items-center gap-x-4 px-4 py-3 transition-colors hover:bg-muted/40 focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset"
              >
                <div className="min-w-0 space-y-0.5">
                  <p className="text-sm font-semibold">{k.name}</p>
                  <p className="text-[13px] text-muted-foreground tabular-nums">
                    {k.cpa != null ? `$${Math.round(k.cpa)} a purchase, last 30 days` : 'No purchases yet'} · {k.files} {k.files === 1 ? 'video' : 'videos'}
                    {!k.makeable && ' · filmed by a creator'}
                  </p>
                </div>
                <KindStatus k={k} />
                <ChevronRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
