import Link from 'next/link';
import { cn } from 'cn';
import type { AccountReadTest, AccountReadUnit } from '@/lib/types';

const METRIC: Record<string, string> = {
  hook_rate: 'hook rate',
  thruplay_rate: 'thruplay rate',
  link_ctr: 'link CTR',
  purchases_per_1k: 'purchases / 1k imp',
  cpa: 'CPA',
};
const fmtVal = (m: string, v: number) =>
  m === 'cpa' ? `$${v.toFixed(0)}` : m === 'purchases_per_1k' ? v.toFixed(2) : `${(100 * v).toFixed(1)}%`;
const fmtChange = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(0)}%`;

export function testLabel(t: AccountReadTest) {
  let v = String(t.value).replace(/_/g, ' ');
  if (t.bin_edges) {
    const [lo, hi] = t.bin_edges;
    v += t.value === 'low' ? ` (under ${lo})` : t.value === 'mid' ? ` (${lo} to ${hi})` : ` (${hi} and up)`;
  }
  return `${t.dimension.replace(/_/g, ' ')} = ${v}`;
}

/** Small-caps text marks. Grades are the app's own semantics, not the accent hue. */
export const markClass: Record<string, string> = {
  strong: 'text-success',
  suggestive: 'text-foreground',
  directional: 'text-amber-700 dark:text-amber-400',
  thin: 'text-muted-foreground',
  mixed: 'text-muted-foreground',
  answerable: 'text-success',
  'not yet': 'text-muted-foreground',
};

export function Mark({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={cn('text-[11px] font-semibold uppercase tracking-[0.08em]', className)}>{children}</span>
  );
}

export function Flag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-sm border border-amber-500/40 px-1 py-px text-[10px] font-medium uppercase tracking-[0.06em] text-amber-700 dark:text-amber-400">
      {children}
    </span>
  );
}

export function EvidenceList({ ids, tests, units }: { ids: string[]; tests: AccountReadTest[]; units: AccountReadUnit[] }) {
  const bySha = new Map(units.map((u) => [u.sha256, u]));
  return (
    <ol className="mt-3 divide-y divide-border border-y border-border text-[13px] leading-6 text-muted-foreground">
      {ids.map((pid) => {
        // a trimmed list tags each test with its id; a full one is indexed by position
        const t = tests.find((x) => x.pid === pid) ?? (pid.startsWith('P') && !tests.some((x) => x.pid) ? tests[Number(pid.slice(1)) - 1] : undefined);
        if (!t) return null;
        const good = t.metric === 'cpa' ? t.change < 0 : t.change > 0;
        const cell = t.cell_shas
          .map((s) => bySha.get(s))
          .filter((u): u is AccountReadUnit => Boolean(u))
          .sort((a, b) => (b.spend ?? 0) - (a.spend ?? 0))
          .slice(0, 5);
        return (
          <li key={pid} className="grid grid-cols-[2.25rem_1fr] gap-x-2 py-2.5 sm:grid-cols-[2.25rem_1fr_auto]">
            <span className="pt-px font-mono text-xs tabular-nums text-muted-foreground/60">{pid.slice(1)}</span>
            <div className="min-w-0">
              <div className="text-foreground">
                {t.group && <span className="text-primary">within {t.group.replace(/_/g, ' ')} · </span>}
                <span className="font-medium">{testLabel(t)}</span>
              </div>
              <div className="tabular-nums">
                {METRIC[t.metric] ?? t.metric} {fmtVal(t.metric, t.cell_value)} with vs {fmtVal(t.metric, t.comp_value)} without{' '}
                <span className={cn('font-mono font-medium', good ? 'text-success' : 'text-destructive')}>{fmtChange(t.change)}</span> ·{' '}
                {t.n_cell} vs {t.n_complement} files · p {t.p.toFixed(3)}
                {t.confounds.length > 0 && (
                  <span className="ml-2 inline-flex gap-1 align-middle">
                    {t.confounds.map((c) => (
                      <Flag key={c}>{c.replace(/_/g, ' ')}</Flag>
                    ))}
                  </span>
                )}
              </div>
              {cell.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                  {cell.map((u) => (
                    <Link
                      key={u.sha256}
                      href={u.rep_creative_id ? `/creatives/${u.rep_creative_id}` : '#'}
                      className="truncate underline decoration-border underline-offset-[3px] transition-colors hover:text-foreground hover:decoration-foreground"
                    >
                      {u.headline}
                      {u.creative_ids.length > 1 ? ` ×${u.creative_ids.length}` : ''}
                    </Link>
                  ))}
                </div>
              )}
            </div>
            <Mark className={cn('pt-px sm:text-right', markClass[t.grade] ?? markClass.thin)}>{t.grade}</Mark>
          </li>
        );
      })}
    </ol>
  );
}
