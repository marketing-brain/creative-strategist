'use client';

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

export const gradeClass: Record<string, string> = {
  strong: 'bg-success/10 text-success',
  suggestive: 'bg-muted text-muted-foreground',
  directional: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  thin: 'bg-muted text-muted-foreground',
  mixed: 'bg-muted text-muted-foreground',
};

export function Pill({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('inline-block rounded-full px-2 py-0.5 text-xs font-semibold', className)}>{children}</span>;
}

export function EvidenceList({ ids, tests, units }: { ids: string[]; tests: AccountReadTest[]; units: AccountReadUnit[] }) {
  const bySha = new Map(units.map((u) => [u.sha256, u]));
  return (
    <ol className="mt-2 space-y-2 border-l-2 border-border pl-3 text-sm text-muted-foreground">
      {ids.map((pid) => {
        const t = pid.startsWith('P') ? tests[Number(pid.slice(1)) - 1] : undefined;
        if (!t) return null;
        const good = t.metric === 'cpa' ? t.change < 0 : t.change > 0;
        const cell = t.cell_shas
          .map((s) => bySha.get(s))
          .filter((u): u is AccountReadUnit => Boolean(u))
          .sort((a, b) => (b.spend ?? 0) - (a.spend ?? 0))
          .slice(0, 5);
        return (
          <li key={pid}>
            <span className="font-mono text-xs text-muted-foreground/70">{pid.slice(1)}</span>{' '}
            {t.group && <span className="text-xs text-primary">within {t.group.replace(/_/g, ' ')} · </span>}
            <b className="font-medium text-foreground">{testLabel(t)}</b> · {METRIC[t.metric] ?? t.metric}{' '}
            {fmtVal(t.metric, t.cell_value)} with vs {fmtVal(t.metric, t.comp_value)} without{' '}
            <span className={cn('font-mono font-medium', good ? 'text-success' : 'text-destructive')}>{fmtChange(t.change)}</span> ·{' '}
            {t.n_cell} vs {t.n_complement} files · p {t.p.toFixed(3)}{' '}
            <Pill className={gradeClass[t.grade] ?? gradeClass.thin}>{t.grade}</Pill>
            {t.confounds.map((c) => (
              <Pill key={c} className="ml-1 bg-amber-500/10 text-amber-700 dark:text-amber-400">
                {c.replace(/_/g, ' ')}
              </Pill>
            ))}
            {cell.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                {cell.map((u) => (
                  <Link
                    key={u.sha256}
                    href={u.rep_creative_id ? `/creatives/${u.rep_creative_id}` : '#'}
                    className="underline decoration-muted-foreground/50 underline-offset-2 hover:decoration-foreground"
                  >
                    {u.headline}
                    {u.creative_ids.length > 1 ? ` ×${u.creative_ids.length}` : ''}
                  </Link>
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
