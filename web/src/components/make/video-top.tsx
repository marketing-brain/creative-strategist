'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ChevronRight, Crosshair, FileText, Loader2, Scissors } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { api, ApiError } from '@/lib/api';
import { splitLede } from '@/lib/account-read-view';
import { clock } from '@/lib/make-format';
import type { MakeFocus, MakeIdea, MakePart, MakeVerdict, MakeVerdictStep, MakeVideo } from '@/lib/types';

// The top of a video's Make page: is it good, where is it weak, and (when opened from What
// works) where in this video the finding applies and the edit that acts on it.

const PART_LABEL: Record<MakePart, string> = { opening: 'Opening', middle: 'Middle', close: 'Close' };
const STEP_LABEL: Record<string, string> = { stop: 'stops the scroll', click: 'earns the click', buy: 'turns clicks into buyers' };
const KIND: Record<MakeVerdict['kind'], string> = {
  good: 'border-success/40 bg-success/10 text-success',
  even: 'border-foreground/25 bg-muted text-foreground',
  weak: 'border-destructive/40 bg-destructive/10 text-destructive',
  unknown: 'border-border text-muted-foreground',
};
const pct = (x: number) => `${Math.round(x * 100)}%`;
const signed = (x: number) => `${x >= 1 ? '+' : '−'}${Math.abs(Math.round((x - 1) * 100))}%`;

function Against({ s, label }: { s: MakeVerdictStep | undefined; label: string }) {
  if (!s || s.ratio == null) return null;
  return (
    <span className={cn('block text-sm', s.state === 'strong' ? 'text-success' : s.state === 'weak' ? 'text-destructive' : 'text-muted-foreground')}>
      {signed(s.ratio)} on {label}
    </span>
  );
}

/** The verdict leads with the video's cluster (like for like); each step also shows the account. */
export function VerdictPanel({ v }: { v: MakeVerdict }) {
  const acct = v.other ?? null;
  const vsCluster = v.baseline?.kind === 'cluster';
  const accountCpa = vsCluster ? acct?.base_cpa : v.base_cpa;
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className={cn('rounded-md border px-2 py-1 text-sm leading-none font-semibold', KIND[v.kind])}>{v.headline}</span>
        {v.advice && <p className="text-sm font-medium">{v.advice}</p>}
      </div>
      {v.steps.length > 0 && (
        <div className="space-y-2 text-sm">
          {v.cpa != null && (
            <p className="text-muted-foreground tabular-nums">
              {`$${Math.round(v.cpa)} a purchase against ${vsCluster && v.base_cpa != null ? `its cluster's $${Math.round(v.base_cpa)} and ` : ''}${accountCpa != null ? `the account's $${Math.round(accountCpa)}` : 'the account'}, last 30 days`}
            </p>
          )}
          {vsCluster && (
            <p className="text-[13px] text-muted-foreground">
              Judged against the {v.baseline!.videos} other videos in its cluster, {v.baseline!.label}.
            </p>
          )}
          <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-xl border border-border bg-border">
            {v.steps.map((s) => (
              <div key={s.step} className={cn('bg-card p-3', s.state === 'weak' && 'bg-destructive/5', s.state === 'strong' && 'bg-success/5')}>
                <dt className="text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">{PART_LABEL[s.part]}</span> {STEP_LABEL[s.step]}
                </dt>
                <dd className="mt-1 tabular-nums">
                  <span className="text-lg font-semibold">{s.value != null ? pct(s.value) : 'No data'}</span>
                  <Against s={s} label={vsCluster ? 'its cluster' : 'the account'} />
                  {vsCluster && <Against s={acct?.steps.find((y) => y.step === s.step)} label="the account" />}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}

export const PLAYER_ID = 'make-player';

function seek(s: number) {
  const el = document.getElementById(PLAYER_ID) as HTMLVideoElement | null;
  if (!el) return;
  el.currentTime = s;
  el.play().catch(() => {});
}

/** The video on a time axis, across the page: its three parts (weak one
 *  tinted), the watch curve, what is said when (click a line to play from it), the finding's
 *  line, and a marker for every idea at the stretch it changes. */
export function Timeline({ v }: { v: MakeVideo }) {
  const t = v.timeline;
  const d = t.duration || 1;
  const x = (s: number) => `${Math.max(0, Math.min(100, (s / d) * 100))}%`;
  const w = (a: number, b: number) => `${Math.max(0.6, ((b - a) / d) * 100)}%`;
  const found = new Set((v.focus?.found ?? []).map((f) => f.start));
  const ideas = v.ideas.filter((i) => i.span && i.decision !== 'skipped');
  // Markers that do not overlap share a row.
  const lanes: number[] = [];
  const laneOf = new Map<string, number>();
  for (const i of [...ideas].sort((a, b) => a.span!.start - b.span!.start)) {
    let k = lanes.findIndex((end) => end < i.span!.start - d * 0.01);
    if (k < 0) k = lanes.push(0) - 1;
    lanes[k] = Math.max(i.span!.end, i.span!.start + d * 0.04);
    laneOf.set(i.ref, k);
  }
  const pts = v.curve.filter(([s, val]) => s <= d && val > 0);
  const path = pts.length > 2 ? pts.map(([s, val], k) => `${k ? 'L' : 'M'}${((s / d) * 100).toFixed(2)} ${(100 - val * 100).toFixed(2)}`).join(' ') : null;
  const [hover, setHover] = useState<string | null>(null);

  return (
    <section className="space-y-1.5" aria-label="The video, second by second">
      <div className="relative rounded-xl border border-border bg-card p-2.5">
        {/* Parts */}
        <div className="relative h-5 text-[11px] font-medium">
          {(Object.keys(t.parts) as MakePart[]).map((p) => {
            const [a, b] = t.parts[p];
            const state = v.verdict.weak === p ? 'weak' : v.verdict.strong.includes(p) ? 'strong' : null;
            return (
              <div
                key={p}
                title={`${PART_LABEL[p]}${state ? `, ${state}` : ''}: ${clock(a)} to ${clock(b)}`}
                className={cn('absolute inset-y-0 truncate rounded-sm px-1 leading-5', state === 'weak' ? 'bg-destructive/15 text-destructive' : state === 'strong' ? 'bg-success/15 text-success' : 'bg-muted text-muted-foreground')}
                style={{ left: x(a), width: w(a, b) }}
              >
                {PART_LABEL[p]}
              </div>
            );
          })}
        </div>
        {/* Watch curve */}
        {path && (
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="mt-1.5 block h-10 w-full" role="img" aria-label="Share of viewers still watching, by second">
            <path d={`${path} L100 100 L0 100 Z`} className="fill-primary/10" />
            <path d={path} fill="none" className="stroke-primary" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
            {v.steepest_drop && (
              <rect x={(v.steepest_drop.start / d) * 100} y={0} width={((v.steepest_drop.end - v.steepest_drop.start) / d) * 100} height={100} className="fill-destructive/10" />
            )}
          </svg>
        )}
        {/* What is said */}
        {t.sentences.length > 0 && (
          <div className="relative mt-1 h-5">
            {t.sentences.map((s) => (
              <button
                key={s.start}
                type="button"
                onClick={() => seek(s.start)}
                onMouseEnter={() => setHover(`${clock(s.start)}  “${s.text}”`)}
                onFocus={() => setHover(`${clock(s.start)}  “${s.text}”`)}
                onMouseLeave={() => setHover(null)}
                aria-label={`Play from ${clock(s.start)}: ${s.text}`}
                className={cn(
                  'absolute inset-y-0 rounded-sm border focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                  found.has(s.start) ? 'border-primary bg-primary/30' : 'border-border bg-muted hover:bg-muted-foreground/20',
                )}
                style={{ left: x(s.start), width: w(s.start, s.end) }}
              />
            ))}
          </div>
        )}
        {/* Ideas */}
        {ideas.length > 0 && (
          <div className="relative mt-1.5" style={{ height: `${lanes.length * 13}px` }}>
            {ideas.map((i: MakeIdea) => (
              <a
                key={i.ref}
                href={`#idea-${i.code}`}
                onMouseEnter={() => setHover(`${i.code}: ${i.span!.how} ${clock(i.span!.start)} to ${clock(i.span!.end)}. ${i.title}`)}
                onFocus={() => setHover(`${i.code}: ${i.span!.how} ${clock(i.span!.start)} to ${clock(i.span!.end)}. ${i.title}`)}
                onMouseLeave={() => setHover(null)}
                className={cn(
                  'absolute h-2.5 overflow-hidden rounded-sm text-[8px] leading-[10px] font-semibold text-white focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                  i.aim === 'weak' ? 'bg-primary' : i.aim === 'strong' ? 'bg-chart-2' : 'bg-foreground/60',
                )}
                style={{ top: `${laneOf.get(i.ref)! * 13}px`, left: x(i.span!.start), width: w(i.span!.start, i.span!.end) }}
                aria-label={`${i.code}: ${i.title}, ${i.span!.how} ${clock(i.span!.start)} to ${clock(i.span!.end)}`}
              >
                <span className="px-0.5">{i.code}</span>
              </a>
            ))}
          </div>
        )}
        <div className="mt-1 flex justify-between font-mono text-[11px] text-muted-foreground tabular-nums">
          <span>0:00</span>
          <span>{clock(d)}</span>
        </div>
      </div>
      <p className="min-h-4 text-[12px] leading-4 text-muted-foreground" aria-live="polite">
        {hover ??
          (t.sentences.length > 0
            ? 'Click a line to play from it. Markers show the stretch each idea changes.'
            : 'Line-by-line timing appears once the brief is written.')}
      </p>
    </section>
  );
}

/** Opened from What works: the finding, where it sits in this video, and the edit that acts on it. */
export function FocusCard({ v, f }: { v: MakeVideo; f: MakeFocus }) {
  const router = useRouter();
  const [busy, setBusy] = useState<'cut' | 'brief' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cutIdea = v.ideas.find((i) => i.code.startsWith('F') && (i.change ?? '').includes(f.title));
  const brief = v.brief;
  const briefDone = brief?.status === 'done';
  const canCut = f.direction === 'avoid' && (f.found === null || f.found.length > 0);

  const cut = async () => {
    setBusy('cut');
    setError(null);
    try {
      await api.makeFindingEdit(v.footage_id, f.id);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not build the edit.');
    } finally {
      setBusy(null);
    }
  };
  const write = async () => {
    setBusy('brief');
    setError(null);
    try {
      await api.createBrief(v.ad_account_id, v.footage_id, f.id);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start the brief.');
      setBusy(null);
    }
  };

  return (
    <section className="space-y-3 rounded-xl border border-primary/30 bg-primary/5 p-4" aria-labelledby="focus-title">
      <p className="text-[13px] text-muted-foreground">
        <span className="font-semibold text-primary">Do this first</span> · from What works · {f.direction === 'avoid' ? 'avoid' : 'do'} ·{' '}
        {f.tier === 'rule' ? 'holds up' : 'a lead'}
      </p>
      <p id="focus-title" className="text-base font-semibold text-balance">{f.title}</p>

      {f.found && f.found.length > 0 && (
        <div className="space-y-1">
          {f.found.map((s) => (
            <p key={s.start} className="flex items-start gap-2 text-sm">
              <Crosshair className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
              <span>
                <span className="font-mono text-muted-foreground tabular-nums">{clock(s.start)}</span> “{s.text}”{' '}
                <span className="text-muted-foreground">(probably; check by listening)</span>
              </span>
            </p>
          ))}
        </div>
      )}
      {f.found && f.found.length === 0 && (
        <p className="text-[13px] text-muted-foreground">No line in this video clearly carries it. Pick another video from What works, or write variants around it.</p>
      )}

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap items-center gap-3">
        {cutIdea ? (
          <a href={`#idea-${cutIdea.code}`} className="text-sm font-medium text-primary hover:underline">
            {cutIdea.code} cuts it. Render it below.
          </a>
        ) : canCut ? (
          <>
            <Button onClick={cut} disabled={busy !== null}>
              {busy === 'cut' ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : <Scissors />} {f.found === null ? 'Find it and cut it' : 'Cut it'}
            </Button>
            <span className="text-sm text-muted-foreground">
              Free{f.found === null ? ' (first time: under 1 cent to time each line)' : ' and instant'}
            </span>
          </>
        ) : null}
        {!cutIdea && (
          <Button variant="outline" onClick={write} disabled={busy !== null || (brief?.status === 'queued' || brief?.status === 'running')}>
            {busy === 'brief' ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : <FileText />} {briefDone ? 'Write variants around it' : 'Write the brief around it'}
          </Button>
        )}
      </div>

      <details className="group text-[13px]">
        <summary className="inline-flex w-fit cursor-pointer list-none items-center gap-1 text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
          <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" /> About this finding
        </summary>
        <div className="mt-2 space-y-1.5">
          <p className="max-w-prose text-sm">{splitLede(f.body)[0]}</p>
          {f.trait && <p className="text-muted-foreground">Look for: {f.trait}</p>}
        </div>
      </details>
    </section>
  );
}
