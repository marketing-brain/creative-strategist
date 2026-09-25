import { ChevronRight } from 'lucide-react';
import { cn } from 'cn';
import type { ExperimentRule } from '@/lib/types';

// The rules that picked the files, in the order they were applied, each saying who decided
// it and what it removed. A rule Claude chose is marked as open, so it reads as a proposal.
export function ExperimentRules({ rules }: { rules: ExperimentRule[] }) {
  if (!rules.length) return null;
  return (
    <section className="space-y-3" aria-labelledby="rules-title">
      <div>
        <h2 id="rules-title" className="text-lg font-semibold">
          Selection rules
        </h2>
        <p className="text-sm text-muted-foreground">
          How the files were picked, applied in this order. The numbers are measured each time the file list is rebuilt.
        </p>
      </div>
      <ol className="divide-y divide-border rounded-xl border border-border">
        {rules.map((r, i) => (
          <li key={r.title} className="grid gap-2 px-4 py-4 sm:grid-cols-[2rem_minmax(0,1fr)]">
            <span className="text-sm font-semibold text-muted-foreground tabular-nums">{i + 1}</span>
            <div className="min-w-0 space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-semibold">{r.title}</h3>
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 text-xs font-medium',
                    r.decided_by === 'you' ? 'bg-secondary text-secondary-foreground' : 'bg-amber-500/15 text-amber-700 dark:text-amber-400'
                  )}
                >
                  {r.decided_by === 'you' ? 'Agreed with you' : "Claude's choice · open to change"}
                </span>
              </div>
              <p className="max-w-prose text-sm">{r.rule}</p>
              <p className="max-w-prose text-sm text-muted-foreground">{r.why}</p>
              <p className="max-w-prose text-sm font-medium tabular-nums">{r.effect}</p>
              {r.dropped.length > 0 && (
                <details className="group max-w-prose text-sm">
                  <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
                    <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
                    {r.dropped.length} ad sets left out by this rule
                  </summary>
                  <ul className="mt-2 max-h-64 space-y-0.5 overflow-y-auto rounded-lg bg-muted/50 px-3 py-2 text-xs [overflow-wrap:anywhere]">
                    {r.dropped.map((d) => (
                      <li key={d} translate="no">
                        {d}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
