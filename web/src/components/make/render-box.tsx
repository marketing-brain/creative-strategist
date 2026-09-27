'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertTriangle, Download, Film, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api, ApiError } from '@/lib/api';
import type { BriefEdit, BriefRender } from '@/lib/types';
import { clock1 } from './shared';

const sameEdit = (a?: BriefEdit | null, b?: BriefEdit | null) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

// What was rendered, from which file, and a way to render it again. Rendering is free
// (ffmpeg on the worker), so it needs no confirmation.
export function RenderBox({ briefId, code, edit, render, hasOriginal }: {
  briefId: string;
  code: string;
  edit: BriefEdit;
  render?: BriefRender;
  hasOriginal: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const working = busy || render?.status === 'queued' || render?.status === 'running';
  const done = render?.status === 'done' && render.url;
  const stale = !!render && render.status === 'done' && (!sameEdit(render.edit, edit) || (hasOriginal && !render.from_original));
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.renderBriefVariant(briefId, code);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start the render.');
    } finally {
      setBusy(false);
    }
  };
  const tight = [...new Set(edit.tight)];

  return (
    <div className="space-y-3">
      {done ? (
        <video src={render!.url!} controls preload="metadata" className="aspect-[9/16] max-h-[420px] w-full rounded-lg bg-black object-contain" />
      ) : (
        <div className="flex aspect-[9/16] max-h-[420px] w-full items-center justify-center rounded-lg border border-dashed border-border bg-muted/40">
          {working ? (
            <span className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Rendering
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">Not rendered yet</span>
          )}
        </div>
      )}
      {tight.length > 0 && (
        <p className="flex items-start gap-1.5 text-sm text-chart-2">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          No clear pause at {tight.map(clock1).join(', ')}. Listen there.
        </p>
      )}
      {render?.status === 'failed' && <p className="text-sm text-destructive">Render failed: {render.error}</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Button size="sm" variant={done && !stale ? 'outline' : 'default'} disabled={working} onClick={start}>
          {working ? <Loader2 className="animate-spin" /> : <Film />}
          {working ? 'Rendering' : done ? 'Render again' : 'Render'}
        </Button>
        {done && (
          <a href={render!.url!} download={`${code}.mp4`} className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline">
            <Download className="size-4" aria-hidden="true" /> Download
          </a>
        )}
      </div>
      {done && (
        <p className="text-sm text-muted-foreground tabular-nums">
          {render!.duration_s?.toFixed(0)}s, {render!.width}×{render!.height}, {render!.from_original ? 'from your original' : 'from the stored copy'}
          {stale && '. Out of date: render again.'}
        </p>
      )}
    </div>
  );
}
