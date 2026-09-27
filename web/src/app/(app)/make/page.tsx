import Link from 'next/link';
import { cn } from 'cn';
import { ChevronRight, Loader2 } from 'lucide-react';
import { LiveRefresh } from '@/components/live-refresh';
import { Poster, ReasonChips } from '@/components/make/shared';
import { api } from '@/lib/api';
import { shortTitle } from '@/lib/make-format';
import { requireAuth } from '@/lib/require-auth.server';
import type { CreatorScriptRow, MakeInProgress } from '@/lib/types';

export const metadata = { title: 'Make' };

// Make has two starting points. Improve a video: batches of new versions of an ad, by how close
// each is to launch (entered from a finding in What works or "Make new versions" on an ad). New
// video for a kind: a script per kind of video, made into a video. Launched work lives under Tests.
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

const VIEWS = [
  { key: 'improve', href: '/make', label: 'Improve a video' },
  { key: 'new', href: '/make?view=new', label: 'New video for a kind' },
] as const;

function MakeViews({ current }: { current: (typeof VIEWS)[number]['key'] }) {
  return (
    <nav aria-label="Make view" className="inline-flex h-8 items-center rounded-lg bg-muted p-[3px] text-sm font-medium">
      {VIEWS.map((v) => (
        <Link
          key={v.key}
          href={v.href}
          aria-current={current === v.key ? 'page' : undefined}
          className={cn(
            'inline-flex h-full items-center rounded-md px-3 transition-colors',
            current === v.key
              ? 'bg-background text-foreground shadow-sm dark:border dark:border-input dark:bg-input/30'
              : 'text-foreground/60 hover:text-foreground dark:text-muted-foreground dark:hover:text-foreground',
          )}
        >
          {v.label}
        </Link>
      ))}
    </nav>
  );
}

export default async function Make({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  await requireAuth();
  const view = (await searchParams).view === 'new' ? 'new' : 'improve';
  const accounts = await api.accounts();
  const active = accounts.find((a) => a.status === 'active') ?? accounts[0];
  if (!active) return <p className="text-sm text-muted-foreground">Connect an ad account to start.</p>;
  const [o, w] = await Promise.all([api.makeOverview(active.id), view === 'new' ? api.whatWorks().catch(() => null) : null]);
  const kinds = [...(w?.creator_scripts ?? [])].sort((a, b) => (a.cpa ?? 1e9) - (b.cpa ?? 1e9));
  const running = Object.values(o.stages).flat().some((p) => p.running);
  const any = Object.values(o.stages).some((l) => l.length > 0);

  return (
    <div className="max-w-6xl space-y-10">
      <LiveRefresh active={running} everyMs={8000} />
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Make</h1>
          <MakeViews current={view} />
        </div>
        <p className="max-w-prose text-sm text-muted-foreground">
          {view === 'new'
            ? 'A brand-new video of a kind that works, from a script written with what holds up for it. Kinds with the lowest cost per purchase first. Launched videos move to Tests.'
            : 'New versions of an ad you already run, launched against the original. Launched batches move to Tests.'}
        </p>
      </div>

      {view === 'improve' && (
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
      )}

      {view === 'new' && (
        <section className="space-y-3" aria-label="Kinds of video">
          {kinds.length === 0 && <p className="text-sm text-muted-foreground">No kinds of video have been read yet.</p>}
          <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border">
            {kinds.map((k) => (
              <li key={k.cluster}>
                <Link
                  href={`/make/new/${encodeURIComponent(k.cluster)}`}
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
      )}
    </div>
  );
}

function KindStatus({ k }: { k: CreatorScriptRow }) {
  const v = k.video;
  const [label, cls] = v?.experiment_id
    ? ['Launched', 'border-success/40 text-success']
    : v?.status === 'done'
      ? ['Made', 'border-success/40 text-success']
      : v?.status === 'confirm'
        ? ['Waiting for you', 'border-primary/40 text-primary']
        : v?.status === 'queued' || v?.status === 'running'
          ? ['Making', 'border-primary/40 text-primary']
          : v?.status === 'blocked' || v?.status === 'failed'
            ? ['Stopped', 'border-destructive/40 text-destructive']
            : k.script
              ? ['Script written', 'border-foreground/25 text-foreground']
              : ['Not started', 'border-border text-muted-foreground'];
  return <span className={`w-fit rounded-sm border px-1.5 py-0.5 text-[12px] leading-none font-medium whitespace-nowrap ${cls}`}>{label}</span>;
}
