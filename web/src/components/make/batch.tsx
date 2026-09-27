'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { AlertTriangle, Check, ChevronDown, ChevronUp, Copy, Download, Film, Loader2, Rocket, SplitSquareHorizontal, Upload, Wand2, X } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { api, ApiError } from '@/lib/api';
import { money } from '@/lib/make-format';
import type { MakeFix, MakeIdea, MakeIdeaState, MakePart, MakeVideo } from '@/lib/types';
import { clock } from '@/lib/make-format';
import { Composer } from './openings';

// Make as a batch builder: every variant of a video is one kind of card (an idea), the batch
// holds the ones picked to launch together against the original, and launching turns the batch
// into a test. Status is shown in form as well as words: Idea, Ready, In batch, Launched.

const STATE: Record<MakeIdeaState, { label: string; cls: string }> = {
  idea: { label: 'Not rendered', cls: 'border-border text-muted-foreground' },
  working: { label: 'Working', cls: 'border-primary/40 text-primary' },
  ready: { label: 'Ready', cls: 'border-success/40 text-success' },
  stale: { label: 'Out of date', cls: 'border-chart-2/50 text-chart-2' },
  failed: { label: 'Failed', cls: 'border-destructive/40 text-destructive' },
  shoot: { label: 'Needs a shoot', cls: 'border-foreground/25 text-foreground' },
};
const KIND: Record<MakeIdea['kind'], string> = {
  reedit: 'Re-edit',
  opening: 'New opening',
  fix: 'Seedance shot',
  pickup: 'Line to shoot',
  script: 'New creator',
};

function Pill({ className, children }: { className: string; children: React.ReactNode }) {
  return <span className={cn('inline-flex w-fit items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[12px] leading-none font-medium whitespace-nowrap', className)}>{children}</span>;
}

async function decide(footageId: string, ref: string, decision: 'in' | 'skipped' | 'none') {
  await api.makeDecide(footageId, ref, decision);
}

function launchable(i: MakeIdea) {
  return i.kind === 'reedit' || i.kind === 'opening' || i.kind === 'fix';
}

/** One slot of the batch. */
function Slot({ i, onRemove }: { i: MakeIdea; onRemove: () => void }) {
  const blocked = i.blockers.length > 0;
  const ready = i.state === 'ready' && !blocked;
  return (
    <li className={cn('relative flex min-h-24 flex-col justify-between gap-2 rounded-lg border bg-card p-3', ready ? 'border-success/40' : blocked ? 'border-destructive/40' : 'border-border')}>
      <div className="flex items-start justify-between gap-2">
        <span className="text-xl leading-none font-semibold text-primary tabular-nums">{i.code}</span>
        <button type="button" onClick={onRemove} aria-label={`Take ${i.code} out of the batch`} className="-m-1 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
          <X className="size-3.5" aria-hidden="true" />
        </button>
      </div>
      <p className="line-clamp-2 text-[13px] leading-snug font-medium">{i.title}</p>
      {ready ? (
        <Pill className="border-success/40 text-success"><Check className="size-3" aria-hidden="true" /> Ready</Pill>
      ) : blocked ? (
        <Pill className="border-destructive/40 text-destructive"><AlertTriangle className="size-3" aria-hidden="true" /> Blocked</Pill>
      ) : (
        <Pill className={STATE[i.state].cls}>{STATE[i.state].label}</Pill>
      )}
    </li>
  );
}

export function BatchBar({ v }: { v: MakeVideo }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [open, setOpen] = useState(false);
  const b = v.batch;
  const chosen = v.ideas.filter((i) => b.chosen.includes(i.ref));
  const empty = Math.max(0, b.max - chosen.length);
  const blockers = chosen.flatMap((i) => i.blockers.map((m) => ({ code: i.code, m })));
  const notReady = chosen.filter((i) => i.state !== 'ready');

  const remove = async (ref: string) => {
    try {
      await decide(v.footage_id, ref, 'none');
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not update the batch.');
    }
  };
  const launch = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.makeLaunch(v.footage_id);
      router.push(`/experiments/${r.experiment_id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not record the launch.');
      setBusy(false);
    }
  };
  const names = chosen.map((i) => i.name ?? `${v.name}-${i.code}`).join('\n');
  const summary =
    chosen.length === 0
      ? `Add up to ${b.max} ideas to test against the original`
      : `${b.ready} of ${chosen.length} ready${blockers.length ? `, ${blockers.length} blocked` : ''}`;

  return (
    <section
      className="sticky bottom-4 z-20 overflow-hidden rounded-xl border border-border bg-card/95 shadow-lg shadow-foreground/5 backdrop-blur supports-[backdrop-filter]:bg-card/85"
      aria-labelledby="batch-title"
    >
      {open && (
        <div id="batch-panel" className="max-h-[60vh] space-y-4 overflow-y-auto border-b border-border p-4">
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <li className="flex min-h-24 flex-col justify-between gap-2 rounded-lg border border-border bg-muted/40 p-3">
          <span className="text-xl leading-none font-semibold text-muted-foreground">A</span>
          <p className="text-[13px] leading-snug font-medium">The original</p>
          <Pill className="border-border text-muted-foreground">Control</Pill>
        </li>
        {chosen.map((i) => <Slot key={i.ref} i={i} onRemove={() => remove(i.ref)} />)}
        {Array.from({ length: empty }, (_, k) => (
          <li key={`e${k}`} className="hidden min-h-24 items-center justify-center rounded-lg border border-dashed border-border text-[13px] text-muted-foreground lg:flex">
            Empty
          </li>
        ))}
      </ol>

      {chosen.length > 0 && (
        <div className="space-y-3 border-t border-border pt-4 text-sm">
          {blockers.length > 0 && (
            <ul className="space-y-1.5">
              {blockers.map((x, k) => (
                <li key={k} className="flex items-start gap-2 text-destructive">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  <span><span className="font-medium">{x.code}:</span> {x.m}</span>
                </li>
              ))}
            </ul>
          )}
          {notReady.length > 0 && blockers.length === 0 && (
            <p className="text-muted-foreground">Render {notReady.map((i) => i.code).join(', ')} to finish the batch.</p>
          )}
          <p className="max-w-prose text-muted-foreground">
            This test runs {b.needs.arms} ads. To judge the openings it needs about {b.needs.hook_impressions.toLocaleString()} impressions in all; to judge cost per purchase, about{' '}
            {b.needs.purchases.toLocaleString()} purchases{b.needs.budget_for_purchases ? `, roughly ${money(b.needs.budget_for_purchases)} at this video's cost per purchase` : ''}.
          </p>
          {error && <p role="alert" className="text-destructive">{error}</p>}
          {b.can_launch && (
            <div className="space-y-3 rounded-lg bg-muted/40 p-3">
              <p className="font-medium">Launch it</p>
              <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
                <li>Download each file below.</li>
                <li>Upload them with the original to one new TESTING ad set, same day, equal budget, using these ad names.</li>
                <li>Come back and record the launch. It becomes a test under Tests.</li>
              </ol>
              <div className="flex flex-wrap gap-x-4 gap-y-2">
                {chosen.map((i) => {
                  const url = i.kind === 'opening' ? i.result_url : i.media_url;
                  return url ? (
                    <a key={i.ref} href={url} download={`${i.name ?? i.code}.mp4`} className="inline-flex items-center gap-1.5 font-medium text-primary hover:underline">
                      <Download className="size-4" aria-hidden="true" /> {i.code}
                    </a>
                  ) : null;
                })}
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 font-medium text-primary hover:underline"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(names);
                      setCopied(true);
                      setTimeout(() => setCopied(false), 1400);
                    } catch {
                      setError('Your browser blocked copying.');
                    }
                  }}
                >
                  {copied ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />} Copy the ad names
                </button>
              </div>
              {confirming ? (
                <div className="flex flex-wrap items-center gap-3">
                  <Button onClick={launch} disabled={busy}>
                    {busy ? <Loader2 className="animate-spin" /> : <Rocket />} Yes, it is live in Meta
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirming(false)} disabled={busy}>Not yet</Button>
                </div>
              ) : (
                <Button onClick={() => setConfirming(true)}>
                  <Rocket /> I&apos;ve launched it
                </Button>
              )}
            </div>
          )}
        </div>
      )}

      {v.launched.length > 0 && (
        <p className="border-t border-border pt-3 text-sm text-muted-foreground">
          Earlier batches:{' '}
          {v.launched.map((l, k) => (
            <span key={k}>
              {k > 0 && ', '}
              {l.experiment_id ? (
                <Link href={`/experiments/${l.experiment_id}`} className="font-medium text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground">
                  {l.title ?? 'a test'}
                </Link>
              ) : (
                l.title
              )}
            </span>
          ))}
        </p>
      )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
        <h2 id="batch-title" className="text-sm font-semibold">Batch</h2>
        <ul className="flex flex-wrap items-center gap-1" aria-label="In the batch">
          <li><Pill className="border-border text-muted-foreground">A original</Pill></li>
          {chosen.map((i) => (
            <li key={i.ref}>
              <Pill className={i.blockers.length ? 'border-destructive/40 text-destructive' : i.state === 'ready' ? 'border-success/40 text-success' : 'border-border text-foreground'}>{i.code}</Pill>
            </li>
          ))}
        </ul>
        <p className={cn('text-sm tabular-nums', blockers.length ? 'text-destructive' : 'text-muted-foreground')}>{summary}</p>

        <Button size="sm" className="ml-auto" variant={b.can_launch && !open ? 'default' : 'outline'} aria-expanded={open} aria-controls="batch-panel" onClick={() => setOpen((o) => !o)}>
          {open ? <><ChevronDown /> Close</> : b.can_launch ? <><Rocket /> Review and launch</> : <><ChevronUp /> Open the batch</>}
        </Button>
      </div>
    </section>
  );
}

/** The original and the new version side by side, both cued to where the change is. */
function Compare({ original, after, span }: { original: string; after: string; span: NonNullable<MakeIdea['span']> }) {
  const [open, setOpen] = useState(true);
  const from = Math.max(0, span.start - 2);
  const to = Math.max(0, span.output_at - 2);
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="inline-flex w-fit items-center gap-1.5 rounded text-sm font-medium text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
        <SplitSquareHorizontal className="size-4" aria-hidden="true" /> Before and after at {clock(span.start)}
      </button>
    );
  }
  return (
    <div className="max-w-sm space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <figure className="space-y-1">
          <video src={`${original}#t=${from}`} controls preload="metadata" className="aspect-[9/16] w-full rounded-lg bg-black object-contain" />
          <figcaption className="text-[12px] text-muted-foreground">Before, from {clock(from)}</figcaption>
        </figure>
        <figure className="space-y-1">
          <video src={`${after}#t=${to}`} controls preload="metadata" className="aspect-[9/16] w-full rounded-lg bg-black object-contain" />
          <figcaption className="text-[12px] text-muted-foreground">After, from {clock(to)}</figcaption>
        </figure>
      </div>
      <button type="button" onClick={() => setOpen(false)} className="rounded text-[13px] text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
        Close the comparison
      </button>
    </div>
  );
}

const PART_TITLE: Record<MakePart, string> = { opening: 'Opening', middle: 'Middle', close: 'Close' };

/** One version of the video as one row: what changes, what it costs, where it stands, and the one
 *  thing to do next. The preview (before and after at the moment of the change) opens in the row. */
function VersionRow({ i, v }: { i: MakeIdea; v: MakeVideo }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inTest = i.decision === 'in';
  const full = v.batch.chosen.length >= v.batch.max && !inTest;
  const brief = v.brief;
  const render = brief?.renders?.[i.code];
  const stale = !!render && render.status === 'done' && JSON.stringify(render.edit ?? null) !== JSON.stringify(i.edit ?? null);
  const working = busy || i.state === 'working';
  // Blockers that apply to every re-edit (the low-resolution copy) are shown once, above the list.
  const own = i.blockers.filter((m) => !m.includes('Upload the original'));

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'That did not work. Try again.');
    } finally {
      setBusy(false);
    }
  };
  const make = () => brief && act(() => api.renderBriefVariant(brief.id, i.code));
  const test = (d: 'in' | 'none') => act(() => decide(v.footage_id, i.ref, d));

  let action: React.ReactNode;
  if (working) {
    action = (
      <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> Making
      </span>
    );
  } else if (i.kind === 'reedit' && (i.state === 'idea' || i.state === 'failed' || stale)) {
    action = (
      <Button size="sm" variant="outline" onClick={make} disabled={!brief}>
        <Film /> {i.state === 'failed' ? 'Try again' : stale ? 'Make again' : 'Make it'}
      </Button>
    );
  } else if (i.state === 'ready') {
    action = (
      <label className={cn('inline-flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1 text-sm font-medium', inTest ? 'border-primary/50 bg-primary/5 text-primary' : 'border-border', full && 'cursor-not-allowed opacity-50')}>
        <input type="checkbox" className="size-3.5 accent-[var(--color-primary)]" checked={inTest} disabled={full} onChange={() => test(inTest ? 'none' : 'in')} />
        Test it
      </label>
    );
  } else {
    action = <span className="text-sm text-muted-foreground">{STATE[i.state].label}</span>;
  }

  return (
    <li id={`idea-${i.code}`} className="scroll-mt-6">
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-4 py-3 sm:grid-cols-[2.5rem_minmax(0,1fr)_5.5rem_auto_1.5rem]">
        <span className="text-lg leading-none font-semibold text-primary tabular-nums">{i.code}</span>
        <button type="button" onClick={() => setOpen((o) => !o)} className="min-w-0 rounded text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
          <span className="block truncate text-sm font-medium">{i.change || i.title}</span>
          <span className="block truncate text-[12px] text-muted-foreground">
            {KIND[i.kind]}
            {i.read_on ? ` · judged on ${i.read_on.charAt(0).toLowerCase() + i.read_on.slice(1)}` : ''}
            {i.aim === 'strong' ? ' · changes a part that already works' : ''}
          </span>
        </button>
        <span className={cn('hidden text-right text-[13px] tabular-nums sm:block', i.cost_usd ? 'text-chart-2' : 'text-muted-foreground')}>{i.cost_label}</span>
        <div className="justify-self-end">{action}</div>
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={open ? 'Hide details' : 'Show details'} className="hidden size-6 items-center justify-center rounded text-muted-foreground hover:bg-muted sm:inline-flex">
          <ChevronDown className={cn('size-4 transition-transform', open && 'rotate-180')} aria-hidden="true" />
        </button>
      </div>
      {open && (
        <div className="space-y-3 border-t border-border bg-muted/20 px-4 py-4 sm:pl-[4.25rem]">
          {i.change && i.title && <p className="text-sm"><span className="font-medium">{i.title}.</span> {i.change}</p>}
          {i.finding && <p className="text-[13px]"><span className="font-medium text-primary">Applies:</span> {i.finding}</p>}
          {i.edit && i.edit.tight?.length > 0 && (
            <p className="flex items-start gap-1.5 text-[13px] text-chart-2">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> No clear pause at {[...new Set(i.edit.tight)].map((t) => clock(t)).join(', ')}. Listen there.
            </p>
          )}
          {[...own, ...(i.kind !== 'reedit' ? i.warnings : [])].map((m, k) => (
            <p key={k} className="flex items-start gap-1.5 text-[13px] text-chart-2">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> {m}
            </p>
          ))}
          {i.state === 'ready' && i.media_url && v.video_url && (i.span ? (
            <Compare original={v.video_url} after={i.media_url} span={i.span} />
          ) : (
            <video src={i.media_url} controls preload="metadata" className="max-h-80 rounded-lg bg-black" />
          ))}
          {i.state === 'working' && i.stage && <p className="text-[13px] text-muted-foreground">{i.stage}</p>}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            {i.state === 'ready' && (i.result_url || i.media_url) && (
              <a href={(i.kind === 'opening' ? i.result_url : i.media_url) ?? '#'} download className="inline-flex items-center gap-1.5 font-medium text-primary hover:underline">
                <Download className="size-4" aria-hidden="true" /> Download
              </a>
            )}
            {i.kind === 'reedit' && i.state === 'ready' && !stale && (
              <button type="button" onClick={make} disabled={busy} className="text-muted-foreground hover:text-foreground">Make again</button>
            )}
            {!inTest && (
              <button type="button" onClick={() => act(() => decide(v.footage_id, i.ref, 'skipped'))} disabled={busy} className="text-muted-foreground hover:text-foreground">
                Skip this version
              </button>
            )}
          </div>
        </div>
      )}
      {error && <p role="alert" className="px-4 pb-3 text-[13px] text-destructive sm:pl-[4.25rem]">{error}</p>}
    </li>
  );
}

const FIX_LABEL: Record<MakeFix['kind'], string> = { hook: 'New first seconds', cutaway: 'Cutaway', end: 'End shot', line: 'On-screen line' };
const TIER_LABEL: Record<string, string> = { rule: 'a rule', test: 'a lead', context: 'shared by this kind' };

/** A fix What works supports for this ad, placed where it is weak. A finding about words becomes an
 *  on-screen line (written in a moment, rendered free); one about what is seen becomes a Seedance
 *  shot (planned, priced, then generated on approval). Once made it becomes a version (S1, S2…). */
function FixRow({ f, v }: { f: MakeFix; v: MakeVideo }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const a = f.attempt;
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'That did not work. Try again.');
    } finally {
      setBusy(false);
    }
  };
  const working = busy || a?.status === 'queued' || a?.status === 'running';
  const cost = a?.cost_usd ?? f.cost_usd;
  const line = f.kind === 'line';
  let action: React.ReactNode;
  if (working) {
    action = (
      <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> {!a ? (line ? 'Writing' : 'Planning') : 'Making'}
      </span>
    );
  } else if (a?.status === 'confirm') {
    action = (
      <Button size="sm" onClick={() => act(() => api.approveFix(a.id))}>
        <Wand2 /> Generate, ${cost.toFixed(2)}
      </Button>
    );
  } else {
    action = (
      <Button size="sm" variant="outline" onClick={() => act(async () => { await api.planFix(v.footage_id, f.key); setOpen(true); })}>
        <Wand2 /> {line ? (a?.status === 'failed' ? 'Write it again' : 'Write it') : a?.status === 'failed' ? 'Plan again' : 'Plan it'}
      </Button>
    );
  }
  return (
    <li>
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-3 sm:grid-cols-[2.5rem_minmax(0,1fr)_5.5rem_auto_1.5rem]">
        <Wand2 className="size-5 text-chart-2" aria-hidden="true" />
        <button type="button" onClick={() => setOpen((o) => !o)} className="min-w-0 rounded text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
          <span className="block truncate text-sm font-medium">
            {FIX_LABEL[f.kind]} over {clock(f.start)}–{clock(f.end)}: {f.untested ? f.trait : `applies “${f.finding}”`}
          </span>
          <span className="block truncate text-[12px] text-muted-foreground">
            {f.untested ? 'Untested idea: no finding for this kind covers it' : `From What works, ${TIER_LABEL[f.tier ?? 'test']}`} · judged on{' '}
            {f.read_on.charAt(0).toLowerCase() + f.read_on.slice(1)} · {line ? 'text on screen, rendered free' : 'Seedance 2.5, the voice plays on'}
          </span>
        </button>
        <span className={cn('hidden text-right text-[13px] tabular-nums sm:block', line ? 'text-muted-foreground' : 'text-chart-2')}>{line ? 'Free' : `~$${cost.toFixed(2)}`}</span>
        <div className="justify-self-end">{action}</div>
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={open ? 'Hide details' : 'Show details'} className="hidden size-6 items-center justify-center rounded text-muted-foreground hover:bg-muted sm:inline-flex">
          <ChevronDown className={cn('size-4 transition-transform', open && 'rotate-180')} aria-hidden="true" />
        </button>
      </div>
      {open && (
        <div className="space-y-2 border-t border-border bg-muted/20 px-4 py-4 text-sm sm:pl-[4.25rem]">
          <p>
            <span className="font-medium">{line ? 'What the line applies:' : 'What it shows:'}</span> {f.trait}
          </p>
          <p className="text-[13px] text-muted-foreground">
            {line
              ? `This finding is about words, so it is applied as a short line on screen over ${clock(f.start)}–${clock(f.end)}${f.weak ? ' (the weak part of this ad)' : ''}, for the many who watch muted. Written from the ad's own claims, checked, and rendered for free.`
              : `A new shot covers ${clock(f.start)}–${clock(f.end)}${f.weak ? ' (the weak part of this ad)' : ''}; the person's words and voice keep playing, so nothing they say changes.`}
          </p>
          {a?.status === 'confirm' && a.prompt && (
            <details className="text-[13px]" open>
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">The exact prompt sent to Seedance 2.5</summary>
              <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-muted/50 p-3 whitespace-pre-wrap">{a.prompt}</pre>
            </details>
          )}
          {a?.status === 'failed' && a.error && <p className="text-[13px] text-destructive">Did not finish: {a.error}</p>}
          {working && a?.stage && <p className="text-[13px] text-muted-foreground">{a.stage}</p>}
        </div>
      )}
      {error && <p role="alert" className="px-4 pb-3 text-[13px] text-destructive sm:pl-[4.25rem]">{error}</p>}
    </li>
  );
}

/** The paid new opening, as a row in the Opening group: describe a setting, see the price, make it. */
function NewOpeningRow({ v }: { v: MakeVideo }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-3 sm:grid-cols-[2.5rem_minmax(0,1fr)_5.5rem_auto_1.5rem]">
        <Wand2 className="size-5 text-chart-2" aria-hidden="true" />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">New opening: a setting you describe</p>
          <p className="truncate text-[12px] text-muted-foreground">Seedance 2.5 redraws the background of the first seconds; the person, words and product stay</p>
        </div>
        <span className="hidden text-right text-[13px] text-chart-2 tabular-nums sm:block">~$1 to $3</span>
        <div className="justify-self-end">
          {!open && (
            <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
              <Wand2 /> Describe it
            </Button>
          )}
        </div>
        <span className="hidden sm:block" />
      </div>
      {open && (
        <div className="border-t border-border bg-muted/20 p-4">
          <Composer accountId={v.ad_account_id} footageId={v.footage_id} onClose={() => setOpen(false)} />
        </div>
      )}
    </li>
  );
}

/** Upload the file that ran, shown once above the list when every re-edit is cut from a small copy. */
function LowResNotice({ v }: { v: MakeVideo }) {
  const router = useRouter();
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lowRes = v.ideas.some((i) => i.blockers.some((m) => m.includes('Upload the original')));
  if (!lowRes || !v.brief) return null;
  const upload = async (file: File | undefined) => {
    if (!file || !v.brief) return;
    setUploading(true);
    setError(null);
    try {
      await api.uploadBriefOriginal(v.brief.id, file);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not upload the file.');
    } finally {
      setUploading(false);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm">
      <AlertTriangle className="size-4 shrink-0 text-destructive" aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="font-medium">Versions are cut from a small stored copy, too small to run as ads.</span> Upload the file that ran, then make them again.
      </span>
      <label className={cn('inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 font-medium hover:bg-muted focus-within:ring-2 focus-within:ring-ring', uploading && 'pointer-events-none opacity-60')}>
        {uploading ? <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Upload className="size-4" aria-hidden="true" />}
        {uploading ? 'Uploading…' : 'Upload the ad file'}
        <input type="file" accept="video/*" className="sr-only" onChange={(e) => upload(e.target.files?.[0])} />
      </label>
      {error && <p role="alert" className="w-full text-destructive">{error}</p>}
    </div>
  );
}

export function Ideas({ v, toolbar, empty }: { v: MakeVideo; toolbar?: React.ReactNode; empty?: React.ReactNode }) {
  const router = useRouter();
  const [copied, setCopied] = useState(false);
  // Markers and links point at #idea-CODE; scroll to it.
  useEffect(() => {
    const reveal = () => {
      const el = location.hash ? document.getElementById(location.hash.slice(1)) : null;
      el?.scrollIntoView({ block: 'center' });
    };
    reveal();
    window.addEventListener('hashchange', reveal);
    return () => window.removeEventListener('hashchange', reveal);
  }, []);
  const live = v.ideas.filter((i) => i.decision !== 'skipped');
  const make = live.filter(launchable);
  const toShoot = live.filter((i) => !launchable(i));
  const fixes = v.fixes ?? [];
  const stateOf = (p: MakePart) => (v.verdict.weak === p ? 'weak' : v.verdict.strong.includes(p) ? 'strong' : 'even');
  const rank = (p: MakePart | null | undefined) => (p == null ? 9 : stateOf(p) === 'weak' ? 0 : stateOf(p) === 'even' ? 1 : 2);
  // Every part gets a group (the opening always has the new-opening row), weak part first.
  const parts = [...new Set<MakePart | null>(['opening', ...make.map((i) => i.part ?? null), ...fixes.filter((f) => !f.untested).map((f) => f.part)])].sort(
    (a, b) => rank(a) - rank(b) || String(a).localeCompare(String(b)),
  );
  const ready = make.filter((i) => i.state === 'ready').length;
  const inTest = make.filter((i) => i.decision === 'in').length;
  const skipped = v.ideas.filter((i) => i.decision === 'skipped');
  const copyLines = async () => {
    const text = toShoot.map((i) => (i.kind === 'script' && i.lines ? i.lines.map((l) => l.text).join(' ') : i.change ?? i.title)).join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* the lines stay on screen to copy by hand */
    }
  };

  return (
    <div className="space-y-8">
      <section className="space-y-3" aria-labelledby="versions-title">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="space-y-0.5">
            <h2 id="versions-title" className="text-base font-semibold">Versions to test</h2>
            <p className="text-sm text-muted-foreground tabular-nums">
              {ready} ready · {inTest} in the test · the weak part first
            </p>
          </div>
          {toolbar}
        </div>
        <LowResNotice v={v} />
        {empty}
        <div className="overflow-hidden rounded-xl border border-border">
          {parts.map((p) => {
            const items = make
              .filter((i) => (i.part ?? null) === p)
              .sort((a, b) => Number(!a.code.startsWith('F')) - Number(!b.code.startsWith('F')));
            const st = p ? stateOf(p) : null;
            return (
              <div key={p ?? 'whole'} className="border-b border-border last:border-0">
                <p className="flex flex-wrap items-baseline gap-x-2 bg-muted/40 px-4 py-2 text-[12px] font-semibold tracking-wide uppercase">
                  {p ? `${PART_TITLE[p]} · ${clock(v.timeline.parts[p][0])} to ${clock(v.timeline.parts[p][1])}` : 'Whole video'}
                  {st === 'weak' && <span className="font-medium tracking-normal text-destructive normal-case">weak: start here</span>}
                  {st === 'strong' && <span className="font-medium tracking-normal text-chart-2 normal-case">already works: change with care</span>}
                </p>
                <ul className="divide-y divide-border">
                  {fixes.filter((f) => f.part === p && !f.untested && f.attempt?.status !== 'done').map((f) => <FixRow key={f.key} f={f} v={v} />)}
                  {items.map((i) => <VersionRow key={i.ref} i={i} v={v} />)}
                  {p === 'opening' && <NewOpeningRow v={v} />}
                  {items.length === 0 && p !== 'opening' && !fixes.some((f) => f.part === p && !f.untested) && (
                    <li className="px-4 py-3 text-sm text-muted-foreground">No versions for this part.</li>
                  )}
                </ul>
              </div>
            );
          })}
          {fixes.some((f) => f.untested && f.attempt?.status !== 'done') && (
            <div className="border-t border-border">
              <p className="bg-muted/40 px-4 py-2 text-[12px] font-semibold tracking-wide uppercase">
                Untested ideas <span className="font-medium tracking-normal text-muted-foreground normal-case">no finding for this kind covers these yet</span>
              </p>
              <ul className="divide-y divide-border">
                {fixes.filter((f) => f.untested && f.attempt?.status !== 'done').map((f) => <FixRow key={f.key} f={f} v={v} />)}
              </ul>
            </div>
          )}
        </div>
        {skipped.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">Skipped ({skipped.length})</summary>
            <ul className="mt-2 flex flex-wrap gap-2">
              {skipped.map((i) => (
                <li key={i.ref}>
                  <button
                    type="button"
                    onClick={async () => {
                      await decide(v.footage_id, i.ref, 'none');
                      router.refresh();
                    }}
                    className="rounded-md border border-border px-2 py-1 hover:bg-muted"
                  >
                    <span className="font-medium">{i.code}</span> {i.title} <span className="text-muted-foreground">(bring back)</span>
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      {toShoot.length > 0 && (
        <section className="space-y-2" aria-labelledby="shoot-title">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="shoot-title" className="text-base font-semibold">
              For your next shoot <span className="font-normal text-muted-foreground tabular-nums">{toShoot.length}</span>
            </h2>
            <Button size="sm" variant="ghost" onClick={copyLines}>
              {copied ? <Check /> : <Copy />} {copied ? 'Copied' : 'Copy the lines'}
            </Button>
          </div>
          <ul className="space-y-1.5 text-sm">
            {toShoot.map((i) => (
              <li key={i.ref} className="flex gap-3">
                <span className="w-8 shrink-0 font-semibold text-primary tabular-nums">{i.code}</span>
                <span>{i.kind === 'script' && i.lines ? i.lines.map((l) => l.text).join(' ') : i.change ?? i.title}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
