import { AlertTriangle, Check, Circle, Hand, Loader2 } from 'lucide-react';
import { cn } from 'cn';
import type { ExperimentStep as Step } from '@/lib/types';

const STATUS: Record<Step['status'], { label: string; tone: string; Icon: typeof Check }> = {
  done: { label: 'Done', tone: 'bg-success/15 text-success', Icon: Check },
  running: { label: 'Running', tone: 'bg-primary/15 text-primary', Icon: Loader2 },
  waiting: { label: 'Waiting on you', tone: 'bg-amber-500/15 text-amber-700 dark:text-amber-400', Icon: Hand },
  failed: { label: 'Failed', tone: 'bg-destructive/15 text-destructive', Icon: AlertTriangle },
  pending: { label: 'Not started', tone: 'bg-muted text-muted-foreground', Icon: Circle },
};

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : null;

// One step on the experiment's timeline. The number on the rail is the real order the steps
// run in; the status chip, progress and cost are written by whatever runs the step.
export function ExperimentStep({ step, n, last }: { step: Step; n: number; last: boolean }) {
  const s = STATUS[step.status];
  const pct = step.progress_total > 0 ? Math.round((step.progress_done / step.progress_total) * 100) : 0;
  const showProgress = step.progress_total > 0 && step.status !== 'pending';
  return (
    <li className="relative flex gap-4">
      <div className="flex flex-col items-center">
        <span
          className={cn(
            'flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold tabular-nums',
            step.status === 'done'
              ? 'border-success bg-success text-white'
              : step.status === 'pending'
                ? 'border-border text-muted-foreground'
                : 'border-foreground text-foreground'
          )}
        >
          {step.status === 'done' ? <Check className="size-3.5" aria-hidden="true" /> : n}
        </span>
        {!last && <span className="w-px flex-1 bg-border" aria-hidden="true" />}
      </div>
      <div className={cn('min-w-0 flex-1 space-y-2', !last && 'pb-6')}>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-semibold">{step.title}</h3>
          <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', s.tone)}>
            <s.Icon className={cn('size-3', step.status === 'running' && 'animate-spin motion-reduce:animate-none')} aria-hidden="true" />
            {s.label}
          </span>
        </div>
        <p className="max-w-prose text-sm text-muted-foreground">{step.detail}</p>
        {step.status === 'waiting' && step.waiting_on && (
          <p className="max-w-prose rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">{step.waiting_on}</p>
        )}
        {showProgress && (
          <div className="flex max-w-md items-center gap-3">
            <div
              className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${step.title} progress`}
            >
              <div
                className={cn('h-full rounded-full transition-[width] duration-500', step.status === 'done' ? 'bg-success' : 'bg-primary')}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="text-xs text-muted-foreground tabular-nums">
              {step.progress_done} of {step.progress_total}
            </span>
          </div>
        )}
        {(step.cost_usd > 0 || step.finished_at || step.started_at) && (
          <p className="text-xs text-muted-foreground tabular-nums">
            {step.cost_usd > 0 && <span>Spent ${step.cost_usd.toFixed(2)}</span>}
            {step.cost_usd > 0 && (step.finished_at || step.started_at) && <span> · </span>}
            {step.finished_at ? <span>Finished {when(step.finished_at)}</span> : step.started_at ? <span>Started {when(step.started_at)}</span> : null}
          </p>
        )}
        {step.result_note && <p className="max-w-prose text-sm">{step.result_note}</p>}
      </div>
    </li>
  );
}
