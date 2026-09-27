'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { AlertTriangle, Check, Clapperboard, Copy, Download, FileText, Loader2, Rocket } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { api, ApiError } from '@/lib/api';
import type { CreatorScript, CreatorScriptRow, ScriptVideo } from '@/lib/types';

// Make › New video for a kind: the kind's script (written from its findings and by watching its
// best videos), the video made from it, and the launch into Tests. Each paid step says its cost
// and asks first.

const dayFmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });
const asText = (s: CreatorScript) =>
  [s.title, s.note, '', ...s.lines.map((l) => `${l.t}  ${l.direction ? `[${l.direction}] ` : ''}${l.text}`)].join('\n');

const money = (x: number) => (x < 1 ? `${Math.round(x * 100)}¢` : `$${x.toFixed(2)}`);

/** Making the script into a video, for kinds a model can make without changing the kind. */
/** Step 3: download, name, put it live in Meta, then record the launch as a test. */
function Launch({ v, row }: { v: ScriptVideo; row: CreatorScriptRow }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const name = `${row.estimate?.mode ? "AIGEN_" : ""}NEW_${row.cluster.replace(/\s*\(.*?\)\s*/g, '').replace(/[^A-Za-z0-9]+/g, '')}_${(row.script?.title ?? 'Script').replace(/[^A-Za-z0-9]+/g, '')}`;
  if (v.experiment_id) {
    return (
      <p className="border-t border-border pt-6 text-sm">
        Launched. <Link href={`/experiments/${v.experiment_id}`} className="font-medium text-primary hover:underline">Follow it in Tests</Link>
      </p>
    );
  }
  const launch = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.launchScriptVideo(v.id);
      router.push(`/experiments/${r.experiment_id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not record the launch.');
      setBusy(false);
    }
  };
  return (
    <section className="space-y-3 border-t border-border pt-6" aria-labelledby="step-launch">
      <h2 id="step-launch" className="text-base font-semibold">3. Launch</h2>
      <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
        <li>Download the video and put it in a TESTING ad set alongside this kind&apos;s current videos, with this ad name.</li>
        <li>Come back and record the launch. It becomes a test under Tests, judged against the other videos of its kind.</li>
      </ol>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <a href={v.url ?? '#'} download={`${name}.mp4`} className="inline-flex items-center gap-1.5 font-medium text-primary hover:underline">
          <Download className="size-4" aria-hidden="true" /> Download
        </a>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 font-medium text-primary hover:underline"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(name);
              setCopied(true);
              setTimeout(() => setCopied(false), 1400);
            } catch {
              setError('Your browser blocked copying.');
            }
          }}
        >
          {copied ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />} Copy the ad name
        </button>
        <code className="rounded bg-muted px-1.5 py-0.5 text-[12px]">{name}</code>
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {v.checks?.too_alike ? (
        <p className="text-sm text-destructive">A shot looks like a video that already ran, so it cannot be launched. Make another.</p>
      ) : confirming ? (
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
    </section>
  );
}

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
          {`First it plans the video (30 seconds at most) and prepares the product shot (about ${money(est.prep_usd)}). Then it shows the exact prompt and cost, about $0.23 a second with Seedance 2.5, and waits for you.`}
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
            {`One Seedance 2.5 generation${v.mode === 'text_only' ? ' with the words as captions' : v.mode === 'voiceover' ? ' with a narrator' : ' with its own voice and lip sync'}: ${money(plan.usd)} on AI Gateway.`}
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
          {v.prompt_text && (
            <details className="text-[13px]">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">The exact prompt sent to the video model</summary>
              <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-muted/50 p-3 whitespace-pre-wrap">{v.prompt_text}</pre>
            </details>
          )}
        </div>
      </div>
      <Button size="sm" onClick={approve} disabled={busy}>
        {busy ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : <Clapperboard />}{' '}
        {v.status === 'blocked' ? 'Try again' : `Generate the video, ${plan ? money(plan.usd) : ''}`}
      </Button>
    </div>
  );

  return (
    <section className="space-y-3 border-t border-border pt-6" aria-labelledby="step-video">
      <h2 id="step-video" className="text-base font-semibold">2. The video</h2>
      {est?.mode === 'talking_head' && (
        <p className="max-w-prose text-[13px] text-muted-foreground">
          Made with a generated presenter who explains the product and never claims personal results (testimonials from people who do not
          exist are not allowed). Meta shows an AI label on it. Name it with AIGEN so its results stay apart from real creators&apos;.
        </p>
      )}
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
            {v.checks?.mirrored && <p className="text-muted-foreground">The label came out mirrored, so the video was flipped.</p>}
            {v.checks?.said_right != null && (
              <p className={cn(v.checks.said_right < 0.9 ? 'text-chart-2' : 'text-muted-foreground')}>
                {v.checks.said_right >= 0.9
                  ? `Says the lines as written (${Math.round(v.checks.said_right * 100)}% match).`
                  : `Listen before using it: what was heard matches the lines ${Math.round(v.checks.said_right * 100)}%. Heard: “${v.checks.heard}”`}
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
      {v?.status === 'done' && v.url && <Launch v={v} row={row} />}
    </section>
  );
}

export function NewVideo({ row }: { row: CreatorScriptRow }) {
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
    <div className="space-y-8">
      <section className="space-y-4" aria-labelledby="step-script">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="step-script" className="text-base font-semibold">1. The script</h2>
          {script?.created_at && <span className="text-[13px] text-muted-foreground">Written {dayFmt.format(new Date(script.created_at))}</span>}
        </div>
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
              {script.presenter && (
                <p className="text-[13px] text-muted-foreground">These are the exact words the presenter will say in the video.</p>
              )}
              {(script.blockers ?? []).length > 0 && (
                <div className="space-y-1 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                  <p className="font-medium">This script cannot be made into a video yet. Write it again.</p>
                  <ul className="list-disc space-y-0.5 pl-5">{script.blockers!.map((b, k) => <li key={k}>{b}</li>)}</ul>
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
      </section>
      {script && !script.too_close && !(script.blockers ?? []).length && <MakeIt row={row} />}
    </div>
  );
}
