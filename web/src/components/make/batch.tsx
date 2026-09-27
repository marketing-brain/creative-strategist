'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { AlertTriangle, Check, ChevronDown, ChevronRight, ChevronUp, Copy, Download, Loader2, Plus, Rocket, SplitSquareHorizontal, Upload, Wand2, X } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { api, ApiError } from '@/lib/api';
import { money } from '@/lib/make-format';
import type { MakeIdea, MakeIdeaState, MakePart, MakeVideo } from '@/lib/types';
import { clock } from '@/lib/make-format';
import { Composer } from './openings';
import { RenderBox } from './render-box';

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
  return i.kind === 'reedit' || i.kind === 'opening';
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
  // Blockers that stop every re-edit, shown once above the batch rather than on each card.
  const lowRes = v.ideas.some((i) => i.blockers.some((m) => m.includes('Upload the original')));
  const [uploading, setUploading] = useState(false);
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
      {lowRes && v.brief && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">
          <AlertTriangle className="size-4 shrink-0 text-destructive" aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className="font-medium">Nothing from this video can launch yet.</span> The stored copy is too small to run as an ad. Upload the file that ran (the same cut), then render again.
          </span>
          <label className={cn('inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 font-medium hover:bg-muted focus-within:ring-2 focus-within:ring-ring', uploading && 'pointer-events-none opacity-60')}>
            {uploading ? <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Upload className="size-4" aria-hidden="true" />}
            {uploading ? 'Uploading…' : 'Upload the original'}
            <input type="file" accept="video/*" className="sr-only" onChange={(e) => upload(e.target.files?.[0])} />
          </label>
        </div>
      )}

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
        {lowRes && v.brief && !open && (
          <button type="button" onClick={() => setOpen(true)} className="inline-flex items-center gap-1 text-sm font-medium text-destructive hover:underline">
            <AlertTriangle className="size-3.5" aria-hidden="true" /> Upload the original to launch
          </button>
        )}
        <Button size="sm" className="ml-auto" variant={b.can_launch && !open ? 'default' : 'outline'} aria-expanded={open} aria-controls="batch-panel" onClick={() => setOpen((o) => !o)}>
          {open ? <><ChevronDown /> Close</> : b.can_launch ? <><Rocket /> Review and launch</> : <><ChevronUp /> Open the batch</>}
        </Button>
      </div>
    </section>
  );
}

/** The original and the new version side by side, both cued to where the change is. */
function Compare({ original, after, span }: { original: string; after: string; span: NonNullable<MakeIdea['span']> }) {
  const [open, setOpen] = useState(false);
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
    <div className="space-y-2">
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

function IdeaCard({ i, v }: { i: MakeIdea; v: MakeVideo }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const inBatch = i.decision === 'in';
  const full = v.batch.chosen.length >= v.batch.max && !inBatch;
  const brief = v.brief;
  const render = brief?.renders?.[i.code];

  const set = async (d: 'in' | 'skipped' | 'none') => {
    setBusy(true);
    setError(null);
    try {
      await decide(v.footage_id, i.ref, d);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not update the batch.');
    } finally {
      setBusy(false);
    }
  };
  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      setError('Your browser blocked copying.');
    }
  };

  return (
    <article id={`idea-${i.code}`} className={cn('flex scroll-mt-6 flex-col gap-3 rounded-xl border bg-card p-4', inBatch ? 'border-primary/50 ring-1 ring-primary/20' : 'border-border')}>
      <div className="flex items-start gap-3">
        <span className="text-2xl leading-none font-semibold text-primary tabular-nums">{i.code}</span>
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="text-base leading-snug font-semibold">{i.title}</p>
          <div className="flex flex-wrap gap-1.5">
            <Pill className="border-border text-muted-foreground">{KIND[i.kind]}</Pill>
            <Pill className={i.cost_usd ? 'border-chart-2/50 text-chart-2' : i.cost_usd === 0 ? 'border-success/40 text-success' : 'border-foreground/25 text-foreground'}>{i.cost_label}</Pill>
            {launchable(i) && <Pill className={STATE[i.state].cls}>{STATE[i.state].label}</Pill>}
          </div>
        </div>
      </div>

      {i.finding && (
        <p className="text-[13px] leading-5">
          <span className="font-medium text-primary">Applies:</span> {i.finding}
        </p>
      )}
      {i.aim === 'strong' && (
        <p className="flex items-start gap-1.5 text-[13px] text-chart-2">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> Risky: it changes the {i.part}, which already works better than the account.
        </p>
      )}
      {i.change && <p className="text-sm leading-6">{i.change}</p>}
      {i.read_on && <p className="text-[13px] text-muted-foreground">Judged on {i.read_on.charAt(0).toLowerCase() + i.read_on.slice(1)}</p>}

      {i.kind === 'reedit' && i.edit && brief && (
        <RenderBox briefId={brief.id} code={i.code} edit={i.edit} render={render} hasOriginal={!!brief.has_original} />
      )}
      {i.kind === 'reedit' && i.state !== 'idea' && i.media_url && i.span && v.video_url && <Compare original={v.video_url} after={i.media_url} span={i.span} />}
      {i.kind === 'opening' &&
        (i.state === 'ready' && i.media_url ? (
          <video src={i.media_url} controls preload="metadata" className="aspect-[9/8] w-full rounded-lg bg-black object-contain" />
        ) : (
          <div className="flex aspect-[9/8] w-full items-center justify-center rounded-lg border border-dashed border-border bg-muted/40 p-4 text-center text-sm text-muted-foreground">
            {i.state === 'working' ? (
              <span className="flex items-center gap-2"><Loader2 className="size-4 animate-spin" aria-hidden="true" /> {i.stage ?? 'Working'}</span>
            ) : (
              'No video'
            )}
          </div>
        ))}
      {i.kind === 'script' && i.lines && (
        <div className="space-y-2 rounded-lg bg-muted/40 p-3">
          {i.lines.map((l, k) => (
            <div key={k} className="grid grid-cols-[2.75rem_minmax(0,1fr)] gap-2 text-[13px] leading-5">
              <span className="font-mono text-muted-foreground tabular-nums">{l.t}</span>
              <span>{l.direction && <span className="text-muted-foreground italic">{l.direction} </span>}{l.text}</span>
            </div>
          ))}
        </div>
      )}

      {i.blockers.map((m, k) => (
        <p key={`b${k}`} className="flex items-start gap-1.5 text-[13px] text-destructive">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> {m}
        </p>
      ))}
      {i.kind !== 'reedit' && i.warnings.map((m, k) => (
        <p key={`w${k}`} className="flex items-start gap-1.5 text-[13px] text-chart-2">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> {m}
        </p>
      ))}
      {error && <p role="alert" className="text-[13px] text-destructive">{error}</p>}

      <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-border pt-3">
        {launchable(i) ? (
          inBatch ? (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => set('none')}>
              <Check /> In the batch
            </Button>
          ) : (
            <Button size="sm" variant="outline" disabled={busy || full} title={full ? `The batch holds ${v.batch.max}` : undefined} onClick={() => set('in')}>
              <Plus /> Add to batch
            </Button>
          )
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              copyText(i.kind === 'script' && i.lines ? i.lines.map((l) => `${l.t}  ${l.direction ? `[${l.direction}] ` : ''}${l.text}`).join('\n') : `${i.change ?? ''}`)
            }
          >
            {copied ? <Check /> : <Copy />} {copied ? 'Copied' : i.kind === 'script' ? 'Copy the script' : 'Copy the line'}
          </Button>
        )}
        {!inBatch && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => set('skipped')}>
            Skip
          </Button>
        )}
        {i.kind === 'opening' && i.state === 'ready' && i.result_url && (
          <a href={i.result_url} download className="ml-auto inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline">
            <Download className="size-4" aria-hidden="true" /> Full ad
          </a>
        )}
      </div>
    </article>
  );
}

function Group({ title, note, tone, count, defaultOpen, children }: {
  title: string;
  note?: string;
  tone?: 'weak' | 'strong' | null;
  count: number;
  defaultOpen: boolean;
  children: React.ReactNode;
}) {
  return (
    <details className="group rounded-xl border border-border" open={defaultOpen || undefined}>
      <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-2 gap-y-1 px-4 py-3 hover:bg-muted/40 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 self-center text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
        <span className="text-sm font-semibold">{title}</span>
        <span className="text-sm text-muted-foreground tabular-nums">{count} {count === 1 ? 'idea' : 'ideas'}</span>
        {note && <span className={cn('text-sm', tone === 'weak' ? 'text-destructive' : tone === 'strong' ? 'text-chart-2' : 'text-muted-foreground')}>{note}</span>}
      </summary>
      <div className="grid gap-4 border-t border-border p-4 sm:grid-cols-2 xl:grid-cols-3">{children}</div>
    </details>
  );
}

export function Ideas({ v, toolbar, empty }: { v: MakeVideo; toolbar?: React.ReactNode; empty?: React.ReactNode }) {
  const router = useRouter();
  const [composing, setComposing] = useState(false);
  // Markers and links point at #idea-CODE; open its group if it is collapsed.
  useEffect(() => {
    const reveal = () => {
      const el = location.hash ? document.getElementById(location.hash.slice(1)) : null;
      const d = el?.closest('details');
      if (el && d && !d.open) {
        d.open = true;
        el.scrollIntoView({ block: 'start' });
      }
    };
    reveal();
    window.addEventListener('hashchange', reveal);
    return () => window.removeEventListener('hashchange', reveal);
  }, []);
  const live = v.ideas.filter((i) => i.decision !== 'skipped');
  const make = live.filter(launchable);
  const toShoot = live.filter((i) => !launchable(i));
  const PART_TITLE: Record<MakePart, string> = { opening: 'Opening', middle: 'Middle', close: 'Close' };
  const stateOf = (p: MakePart) => (v.verdict.weak === p ? 'weak' : v.verdict.strong.includes(p) ? 'strong' : 'even');
  const rank = (p: MakePart | null | undefined) => (p == null ? 9 : stateOf(p) === 'weak' ? 0 : stateOf(p) === 'even' ? 1 : 2);
  const parts = [...new Set(make.map((i) => i.part ?? null))].sort((a, b) => rank(a) - rank(b) || String(a).localeCompare(String(b)));
  const groups = parts.map((p) => ({
    key: p ?? 'whole',
    title: p ? `${PART_TITLE[p]} · ${clock(v.timeline.parts[p][0])} to ${clock(v.timeline.parts[p][1])}` : 'Whole video',
    state: p ? stateOf(p) : null,
    items: make.filter((i) => (i.part ?? null) === p).sort((a, b) => Number(!a.code.startsWith('F')) - Number(!b.code.startsWith('F'))),
  }));
  // One group open: the weak part's (sorted first), plus any holding the finding's edit.
  const openGroup = (g: (typeof groups)[number], k: number) => k === 0 || g.items.some((i) => i.code.startsWith('F'));
  const skipped = v.ideas.filter((i) => i.decision === 'skipped');
  return (
    <section className="space-y-3" aria-labelledby="ideas-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-0.5">
          <h2 id="ideas-title" className="text-base font-semibold">Ideas</h2>
          <p className="text-sm text-muted-foreground">Grouped by the part they change, weak part first.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!composing && (
            <Button variant="outline" size="sm" onClick={() => setComposing(true)}>
              <Wand2 /> New opening <span className="text-muted-foreground">$1 to $6</span>
            </Button>
          )}
          {toolbar}
        </div>
      </div>
      {composing && <Composer accountId={v.ad_account_id} footageId={v.footage_id} onClose={() => setComposing(false)} />}
      {empty}
      {groups.map((g, k) => (
        <Group
          key={g.key}
          title={g.title}
          count={g.items.length}
          tone={g.state === 'weak' || g.state === 'strong' ? g.state : null}
          note={g.state === 'weak' ? 'the weak part: start here' : g.state === 'strong' ? 'already works: change with care' : undefined}
          defaultOpen={openGroup(g, k)}
        >
          {g.items.map((i) => <IdeaCard key={i.ref} i={i} v={v} />)}
        </Group>
      ))}
      {toShoot.length > 0 && (
        <Group title="To shoot" count={toShoot.length} note="for your next shoot, not launchable from here" defaultOpen={groups.length === 0}>
          {toShoot.map((i) => <IdeaCard key={i.ref} i={i} v={v} />)}
        </Group>
      )}
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
  );
}
