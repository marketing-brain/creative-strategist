'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Wand2, X } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { api, ApiError } from '@/lib/api';
import type { GeneratePlan } from '@/lib/types';
import { money2 } from './shared';

// What to change, only: the server wraps it in ByteDance's edit structure (keep the person, lip sync,
// hands, product, on-screen text and timing; process only the background). Setting and lighting
// swaps are what edit models do well.
const TEMPLATE = '';

// One press is one paid call, so the price is on the button and a second press confirms it.
export function Composer({ accountId, footageId, onClose }: { accountId: string; footageId: string; onClose: () => void }) {
  const router = useRouter();
  const [plan, setPlan] = useState<GeneratePlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [prompt, setPrompt] = useState(TEMPLATE);
  const [resolution, setResolution] = useState('720p');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .generatePlan(footageId)
      .then((p) => live && setPlan(p))
      .catch((e) => live && setPlanError(e instanceof ApiError ? e.message : 'Could not read the video.'))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [footageId]);
  const tooShort = prompt.trim().length < 15;
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.createGeneration(accountId, footageId, prompt, resolution);
      onClose();
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start. Try again.');
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <div className="space-y-5 rounded-xl border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-base font-semibold">New opening</p>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}><X /></Button>
      </div>
      {loading && (
        <div className="space-y-2" aria-busy="true">
          <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
          <div className="h-24 animate-pulse rounded-lg bg-muted" />
        </div>
      )}
      {planError && <p className="text-sm text-destructive">{planError}</p>}
      {plan && (
        <>
          <p className="max-w-prose text-sm text-muted-foreground">
            The first {plan.window_s.toFixed(1)}s go to Seedance 2.5.{' '}
            {plan.cut_s != null
              ? `The edit joins the original at its shot change at ${plan.cut_s.toFixed(1)}s.`
              : 'There is no shot change early on, so the edit joins where it stops matching the original.'}{' '}
            The original sound runs under the whole ad.
          </p>
          <div className="space-y-2">
            <label htmlFor="gen-prompt" className="text-sm font-medium">New setting and lighting for the opening</label>
            <Textarea
              id="gen-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="e.g. a dark bedroom at 3am, lit only by a bedside lamp, a phone glowing on the nightstand"
            />
            <p className="text-[12px] text-muted-foreground">
              Only the background changes. The person, what they say, their hands, the product and any on-screen text stay exactly as they are.
            </p>
            <p className="text-sm text-muted-foreground">Edits that leave the person and product alone hold up best. Changing someone&apos;s face or words needs their consent.</p>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Size</legend>
            <div className="flex flex-wrap gap-2">
              {(['480p', '720p', '1080p'] as const).map((r) => (
                <label
                  key={r}
                  className={cn(
                    'flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors',
                    resolution === r ? 'border-primary bg-primary/5' : 'border-border hover:border-foreground/30',
                  )}
                >
                  <input type="radio" name="gen-res" value={r} checked={resolution === r} className="sr-only" onChange={() => { setResolution(r); setConfirming(false); }} />
                  <span className="font-medium">{r}</span>
                  <span className="text-muted-foreground tabular-nums">{money2(plan.cost[r])}</span>
                </label>
              ))}
            </div>
            {plan.height < 1280 && resolution !== '480p' && (
              <p className="text-sm text-muted-foreground">The stored copy is {plan.width}×{plan.height}; a larger size will not add detail.</p>
            )}
          </fieldset>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <div className="flex flex-wrap items-center gap-3">
            {confirming ? (
              <>
                <Button onClick={go} disabled={busy}>
                  <Wand2 /> {busy ? 'Starting' : `Spend ${money2(plan.cost[resolution])}`}
                </Button>
                <Button variant="ghost" onClick={() => setConfirming(false)} disabled={busy}>Cancel</Button>
                <span className="text-sm text-muted-foreground">One paid call to Seedance 2.5 on AI Gateway, not retried if it fails.</span>
              </>
            ) : (
              <Button onClick={() => setConfirming(true)} disabled={tooShort}>
                <Wand2 /> Generate for {money2(plan.cost[resolution])}
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
