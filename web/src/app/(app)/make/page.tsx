import Link from 'next/link';
import { ChevronRight, Loader2 } from 'lucide-react';
import { LiveRefresh } from '@/components/live-refresh';
import { Poster, ReasonChips } from '@/components/make/shared';
import { api } from '@/lib/api';
import { shortTitle } from '@/lib/make-format';
import { requireAuth } from '@/lib/require-auth.server';
import type { MakeInProgress } from '@/lib/types';

export const metadata = { title: 'Make' };

// Make is where batches are built: the videos being worked on, by how close each batch is to
// launch. Once launched, a batch leaves Make and lives under Tests. There are two ways in: a finding in What works, or "Make new versions" on an ad.
const STAGES = [
  { key: 'drafting', title: 'Drafting', empty: 'Nothing being drafted.' },
  { key: 'ready', title: 'Ready to launch', empty: 'No batch ready yet.' },
] as const;

function progress(p: MakeInProgress): string {
  const bits: string[] = [];
  if (p.renders) bits.push(`${p.renders} re-edit${p.renders === 1 ? '' : 's'} rendered`);
  else if (p.briefs) bits.push('Brief written');
  if (p.generations) bits.push(`${p.generations} new opening${p.generations === 1 ? '' : 's'}`);
  return bits.join(', ') || 'Started';
}

export default async function Make() {
  await requireAuth();
  const accounts = await api.accounts();
  const active = accounts.find((a) => a.status === 'active') ?? accounts[0];
  if (!active) return <p className="text-sm text-muted-foreground">Connect an ad account to start.</p>;
  const o = await api.makeOverview(active.id);
  const running = Object.values(o.stages).flat().some((p) => p.running);
  const any = Object.values(o.stages).some((l) => l.length > 0);

  return (
    <div className="max-w-6xl space-y-10">
      <LiveRefresh active={running} everyMs={8000} />
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Make</h1>
        <p className="max-w-prose text-sm text-muted-foreground">
          Build a batch of new versions of a video, then launch it against the original. Launched batches move to Tests. Start from a finding in What works, or from any video in Ads.
        </p>
      </div>

      <section className="space-y-3" aria-label="Your batches">
        {!any ? (
          <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
            No batches yet. Start from a finding in{' '}
            <Link href="/what-works" className="font-medium text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground">What works</Link>, or open any video in{' '}
            <Link href="/creatives" className="font-medium text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground">Ads</Link> and choose Make new versions.
          </p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {STAGES.map((s) => {
              const list = o.stages[s.key];
              return (
                <div key={s.key} className="space-y-2">
                  <h2 className="flex items-baseline gap-2 text-sm font-semibold">
                    {s.title} <span className="font-normal text-muted-foreground tabular-nums">{list.length}</span>
                  </h2>
                  {list.length === 0 ? (
                    <p className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">{s.empty}</p>
                  ) : (
                    <ul className="space-y-2">
                      {list.map((p) => (
                        <li key={p.footage_id}>
                          <Link
                            href={`/make/${p.footage_id}`}
                            className="group flex gap-3 rounded-xl border border-border bg-card p-3 transition-shadow hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
                          >
                            <Poster url={p.poster_url} className="w-12" />
                            <div className="min-w-0 flex-1 space-y-1.5">
                              <p className="truncate text-sm font-semibold">{shortTitle(p.name)}</p>
                              <ReasonChips reasons={p.reasons} />
                              <p className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
                                {p.running && <Loader2 className="size-3.5 animate-spin text-primary" aria-hidden="true" />}
                                {p.running ? 'Working' : progress(p)}
                              </p>
                            </div>
                            <ChevronRight className="size-4 self-center text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

    </div>
  );
}
