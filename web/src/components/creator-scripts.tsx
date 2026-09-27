'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertTriangle, Check, ChevronDown, Clapperboard, Copy, Download, FileText, Loader2 } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { api, ApiError } from '@/lib/api';
import type { CreatorScript, CreatorScriptRow, ScriptVideo } from '@/lib/types';

// A script for a new creator, per kind of video: written from that kind's rules, shared traits
// and its best videos (cs.experiments.creator_script). One model call per click, under a cent;
// the row asks before making it.

const FOCUS = 'focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none';
const dayFmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });
const asText = (s: CreatorScript) =>
  [s.title, s.note, '', ...s.lines.map((l) => `${l.t}  ${l.direction ? `[${l.direction}] ` : ''}${l.text}`)].join('\n');

const money = (x: number) => (x < 1 ? `${Math.round(x * 100)}¢` : `$${x.toFixed(2)}`);

/** Making the script into a video, for kinds a model can make without changing the kind. */
function MakeIt({ row }: { row: CreatorScriptRow }) {
  const router = useRouter();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const v: ScriptVideo | null = row.video ?? null;
  const working = v?.status === 'queued' || v?.status === 'running';
  // While it is being made, look again every few seconds.
  useEffect(() => {
    if (!working) return;
    const t = setInterval(() => router.refresh(), 8000);
    return () => clearInterval(t);
  }, [working, router]);

  if (!row.makeable) {
    return (
      <p className="text-[13px] text-muted-foreground">
        To make: film it with a real creator. A generated person would make it an AI video, judged against the AI kind instead.
      </p>
    );
  }
  const est = row.estimate;
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setAsking(false);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start it.');
    } finally {
      setBusy(false);
    }
  };
  const start = () => act(() => api.makeScriptVideo(row.cluster));
  const approve = () => v && act(() => api.approveScriptVideo(v.id));
  const ask = est && (
    asking ? (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">
          First it plans the shots, records the voice and finds the product (about {money(est.prep_usd)}). Then it shows the exact cost of
          the clips and waits for you.
        </span>
        <Button size="sm" onClick={start} disabled={busy}>
          {busy ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : <Clapperboard />} Yes, start
        </Button>
        {!busy && <Button size="sm" variant="ghost" onClick={() => setAsking(false)}>Cancel</Button>}
      </div>
    ) : (
      <Button size="sm" variant={v?.status === 'done' ? 'outline' : 'default'} onClick={() => setAsking(true)}>
        <Clapperboard /> {v?.status === 'done' ? 'Make another' : 'Make it'}
      </Button>
    )
  );
  const plan = v?.clip_plan;
  const review = v && (v.status === 'confirm' || v.status === 'blocked') && (
    <div className="space-y-3">
      {plan && (
        <p className="text-sm">
          <span className="font-medium">{plan.shots} shots, about {Math.round(plan.video_seconds)}s.</span>{' '}
          <span className="text-muted-foreground">
            The clips cost {money(plan.usd)} on AI Gateway ({plan.seconds}s of generated video).
          </span>
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-[120px_minmax(0,1fr)]">
        {v.product_url && (
          <figure className="space-y-1">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={v.product_url} alt="The product reference passed to the video model" className="w-full rounded-lg border border-border" />
            <figcaption className="text-[12px] text-muted-foreground">Product reference</figcaption>
          </figure>
        )}
        <div className="space-y-2">
          {v.voice_url && <audio src={v.voice_url} controls preload="none" className="w-full" />}
          <ol className="list-decimal space-y-1 pl-5 text-[13px]">
            {v.shots.map((x, k) => (
              <li key={k}>
                {x.prompt} {x.product && <span className="text-muted-foreground">(with the product)</span>}
              </li>
            ))}
          </ol>
        </div>
      </div>
      <Button size="sm" onClick={approve} disabled={busy}>
        {busy ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : <Clapperboard />}{' '}
        {v.status === 'blocked' ? 'Try the clips again' : `Generate the clips, ${plan ? money(plan.usd) : ''}`}
      </Button>
    </div>
  );

  return (
    <div className="space-y-3 border-t border-border pt-4">
      <p className="text-sm font-medium">The video</p>
      {v?.status === 'done' && v.url && (
        <div className="grid gap-4 sm:grid-cols-[200px_minmax(0,1fr)]">
          <video src={v.url} controls preload="metadata" className="aspect-[9/16] w-full rounded-lg bg-black object-contain" />
          <div className="space-y-2 text-sm">
            {v.checks && (
              <p className={cn('flex items-start gap-1.5', v.checks.too_alike ? 'font-medium text-destructive' : 'text-muted-foreground')}>
                {v.checks.too_alike ? <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> : <Check className="mt-0.5 size-4 shrink-0 text-success" aria-hidden="true" />}
                {v.checks.too_alike
                  ? `Shot ${v.checks.shots.filter((x) => x.too_alike).map((x) => x.shot + 1).join(', ')} looks like a video that already ran. Make another before using it.`
                  : `Every shot looks new against ${v.checks.videos} videos of this kind.`}
              </p>
            )}
            <a href={v.url} download className="inline-flex items-center gap-1.5 font-medium text-primary hover:underline">
              <Download className="size-4" aria-hidden="true" /> Download
            </a>
          </div>
        </div>
      )}
      {working && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> {v?.stage ?? 'Waiting to start'}. The page updates by itself.
        </p>
      )}
      {(v?.status === 'blocked' || v?.status === 'failed') && v.error && (
        <p className="flex items-start gap-1.5 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> {v.error}
        </p>
      )}
      {review}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {!working && v?.status !== 'confirm' && v?.status !== 'blocked' && ask}
    </div>
  );
}

function Row({ row }: { row: CreatorScriptRow }) {
  const router = useRouter();
  const [script, setScript] = useState<CreatorScript | null>(row.script);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const write = async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await api.writeCreatorScript(row.cluster);
      setScript({ ...s, created_at: new Date().toISOString() });
      setAsking(false);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not write the script.');
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    if (!script) return;
    try {
      await navigator.clipboard.writeText(asText(script));
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      setError('Your browser blocked copying. Select the script and copy it by hand.');
    }
  };

  const ask = asking ? (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted-foreground">Watches its two best videos (about 1 cent each, the first time) and writes the script (under 1 cent).</span>
      <Button size="sm" onClick={write} disabled={busy}>
        {busy ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : <FileText />} {busy ? 'Watching and writing, about a minute' : 'Yes, write it'}
      </Button>
      {!busy && <Button size="sm" variant="ghost" onClick={() => setAsking(false)}>Cancel</Button>}
    </div>
  ) : (
    <Button size="sm" variant={script ? 'outline' : 'default'} onClick={() => setAsking(true)}>
      <FileText /> {script ? 'Write it again' : 'Write the script'}
    </Button>
  );

  return (
    <li>
      <details className="group">
        <summary
          className={cn(
            'grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto_1rem] items-center gap-x-4 px-4 py-4 transition-colors hover:bg-muted/40 [&::-webkit-details-marker]:hidden',
            FOCUS,
            'focus-visible:ring-inset',
          )}
        >
          <div className="min-w-0 space-y-1">
            <h3 className="text-base leading-snug font-semibold">{row.name}</h3>
            <p className="text-[13px] text-muted-foreground">
              {script ? script.title : 'No script yet'} · {row.files} {row.files === 1 ? 'video' : 'videos'} of this kind
            </p>
          </div>
          <span
            className={cn(
              'w-fit rounded-sm border px-1.5 py-0.5 text-[12px] leading-none font-medium whitespace-nowrap',
              script ? 'border-success/40 text-success' : 'border-border text-muted-foreground',
            )}
          >
            {script ? `Written${script.created_at ? ` ${dayFmt.format(new Date(script.created_at))}` : ''}` : 'Not written'}
          </span>
          <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
        </summary>

        <div className="space-y-4 px-4 pb-4">
          {!script ? (
            <p className="max-w-prose text-sm text-muted-foreground">
              A new creator making this kind of video: written from its rules and shared traits, and the voice and claims of its best videos.
            </p>
          ) : (
            <>
              <p className="max-w-prose text-sm">{script.note}</p>
              <div className="space-y-2 rounded-lg bg-muted/40 p-3">
                {script.lines.map((l, k) => (
                  <div key={k} className="grid grid-cols-[3rem_minmax(0,1fr)] gap-3 text-sm">
                    <span className="font-mono text-muted-foreground tabular-nums">{l.t}</span>
                    <span>
                      {l.direction && <span className="text-muted-foreground italic">{l.direction} </span>}
                      {l.text}
                    </span>
                  </div>
                ))}
              </div>
              {script.follows.length > 0 && (
                <div className="space-y-1.5 text-sm">
                  <p className="font-medium">What it applies</p>
                  <ul className="space-y-1">
                    {script.follows.map((f) => (
                      <li key={f.id} className="flex items-start gap-2">
                        <Check className="mt-0.5 size-4 shrink-0 text-success" aria-hidden="true" />
                        <span>
                          <span className="font-medium">{f.title}.</span> <span className="text-muted-foreground">{f.how}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {script.too_close && script.closest && (
                <p className="flex items-start gap-1.5 text-sm font-medium text-destructive">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  Too close to a video that already ran: {Math.round(script.closest.ratio * 100)}% the same words as {script.closest.name ?? 'one of them'}. Write it again.
                </p>
              )}
              {[...script.missed.map((m) => `It does not say how it follows the rule “${m.title}”. Check before filming.`), ...script.dropped].map((m, k) => (
                <p key={k} className="flex items-start gap-1.5 text-[13px] text-chart-2">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> {m}
                </p>
              ))}
              {script.claims.length > 0 && (
                <div className="space-y-1 rounded-lg bg-chart-2/10 p-3 text-sm">
                  <p className="font-medium">For claims review before filming</p>
                  <ul className="list-disc space-y-0.5 pl-5">{script.claims.map((c, k) => <li key={k}>{c}</li>)}</ul>
                </div>
              )}
              {script.from_videos.length > 0 && (
                <p className="text-[13px] break-words text-muted-foreground">
                  Learned from {script.from_videos.some((v) => v.watched) ? 'watching ' : ''}
                  {script.from_videos.map((v) => v.name ?? 'a video').join('; ')}
                </p>
              )}
              {script.closest && !script.too_close && (
                <p className="text-[13px] break-words text-muted-foreground">
                  New wording: the closest of the {script.compared_with} videos of this kind shares {Math.round(script.closest.ratio * 100)}% of its words ({script.closest.name}).
                </p>
              )}
            </>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <div className="flex flex-wrap items-center gap-2">
            {script && !asking && (
              <Button size="sm" variant="secondary" onClick={copy}>
                {copied ? <Check /> : <Copy />} {copied ? 'Copied' : 'Copy the script'}
              </Button>
            )}
            {ask}
          </div>
          {script && <MakeIt row={row} />}
        </div>
      </details>
    </li>
  );
}

export function CreatorScripts({ rows }: { rows: CreatorScriptRow[] }) {
  if (!rows.length) return null;
  return (
    <section className="space-y-3" aria-labelledby="creator-scripts">
      <div className="space-y-1">
        <h2 id="creator-scripts" className="flex items-baseline gap-2 text-base font-semibold">
          Scripts for a new creator <span className="font-normal text-muted-foreground tabular-nums">{rows.length}</span>
        </h2>
        <p className="max-w-prose text-[13px] text-muted-foreground">
          One per kind of video, for someone new to film. Each follows what holds up for its kind and borrows the voice of its best videos.
        </p>
      </div>
      <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border">
        {rows.map((r) => <Row key={r.cluster} row={r} />)}
      </ul>
    </section>
  );
}
