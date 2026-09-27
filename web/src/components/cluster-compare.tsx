import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { cn } from 'cn';
import { Thumb } from '@/components/adset-insights';
import { ScopeLabel } from '@/components/scope-label';
import type { Formatters } from '@/lib/format';
import type { ClusterCard, ClusterCompare as Data, ClusterMetric } from '@/lib/types';

// Clusters against each other as a handful of decisions, the same shape as the cards inside one
// cluster: a plain title, the numbers that prove it, one line of why, and the cluster's top ads.
// Graded the way the Insights tab grades a pattern; the full table sits folded underneath.

const KIND: Record<ClusterCard['kind'], { label: string; tone: string }> = {
  scale: { label: 'Scale', tone: 'bg-success/15 text-success' },
  attention_not_sale: { label: 'Attention, not sales', tone: 'bg-chart-2/20 text-chart-2' },
  cut: { label: 'Cut or rework', tone: 'bg-destructive/15 text-destructive' },
  weak_steps: { label: 'Weaker funnel', tone: 'bg-chart-2/20 text-chart-2' },
  strong_steps: { label: 'Stronger funnel', tone: 'bg-muted text-muted-foreground' },
};
const ORDER: ClusterMetric['metric'][] = ['cpa', 'purchases_per_1k', 'hook_rate', 'thruplay_rate', 'link_ctr'];
const SHORT: Record<ClusterMetric['metric'], string> = {
  cpa: 'Cost / purchase',
  purchases_per_1k: 'Purchases / 1k',
  hook_rate: 'Stop in first 3s',
  thruplay_rate: 'ThruPlay',
  link_ctr: 'Link CTR',
};
const HOLDS = new Set(['strong', 'suggestive']);

const signed = (x: number) => `${x >= 0 ? '+' : '−'}${Math.round(Math.abs(x) * 100)}%`;

function show(metric: ClusterMetric['metric'], v: number | null, fmt: Formatters) {
  if (v == null) return '–';
  if (metric === 'cpa') return fmt.money(v);
  return `${(v * 100).toFixed(1)}%`;
}

function value(m: ClusterMetric, v: number, fmt: Formatters) {
  if (m.metric === 'cpa') return fmt.money(v);
  if (m.metric === 'purchases_per_1k') return v.toFixed(2);
  return `${(v * 100).toFixed(1)}%`;
}

// Colour only when the result holds up; a result that could be chance stays plain.
const tone = (m: ClusterMetric) => (!HOLDS.has(m.grade) ? 'text-foreground' : m.better ? 'text-success' : 'text-destructive');

const BADGE = {
  holds: { label: 'Checked', cls: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200' },
  lead: { label: 'Lead', cls: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200' },
  context: { label: 'Context', cls: 'bg-muted text-muted-foreground' },
} as const;

// A cluster whose content has been read: its findings, in place of the generic funnel card.
function ContentCard({ c, name, readHref }: { c: ClusterCard; name: string; readHref: string }) {
  return (
    <article className="grid gap-x-6 gap-y-3 border-t border-border py-6 first:border-t-0 first:pt-0 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0 space-y-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-1 shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[11px] font-semibold text-primary">Content read</span>
          <h3 className="text-lg leading-snug font-semibold tracking-tight text-balance">{name}</h3>
        </div>
        <ul className="space-y-2">
          {(c.content ?? []).map((x, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[15px] leading-6">
              <span className={cn('shrink-0 rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em]', BADGE[x.strength].cls)}>
                {BADGE[x.strength].label}
              </span>
              <span className="min-w-0">{x.title}</span>
              <span className="font-mono text-[12px] tabular-nums text-muted-foreground">
                {x.p != null ? (x.p < 0.001 ? 'p < 0.001' : `p = ${x.p.toFixed(3)}`) : 'no test'}
              </span>
            </li>
          ))}
        </ul>
        <Link href={readHref} className="inline-block text-sm font-medium text-primary hover:underline">
          Read this cluster →
        </Link>
      </div>
      <div className="grid w-full grid-cols-3 gap-2 sm:w-56">
        {c.thumbs.map((t) => (
          <Thumb key={t.id} row={t} />
        ))}
      </div>
    </article>
  );
}

function Card({ c, fmt, readHref }: { c: ClusterCard; fmt: Formatters; readHref: string }) {
  const k = KIND[c.kind];
  return (
    <article className="grid gap-x-6 gap-y-3 border-t border-border py-6 first:border-t-0 first:pt-0 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0 space-y-3">
        <div className="flex items-start gap-2.5">
          <span className={cn('mt-1 shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold', k.tone)}>{k.label}</span>
          <h3 className="text-lg leading-snug font-semibold tracking-tight text-balance">{c.title}</h3>
        </div>
        {/* The same four numbers on every card, in the same place, so cards read down the list. */}
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
          {c.stats.map((m) => (
            <div key={m.metric}>
              <dt className="text-xs text-muted-foreground">{SHORT[m.metric]}</dt>
              <dd className="tabular-nums">
                <span
                  className={cn('text-base font-semibold', m.holds ? (m.better ? 'text-success' : 'text-destructive') : 'text-foreground')}
                  title={m.grade ? `Against the rest of the account: ${m.grade}` : undefined}
                >
                  {show(m.metric, m.mine, fmt)}
                </span>
                <span className="ml-1.5 text-sm text-muted-foreground">account {show(m.metric, m.account, fmt)}</span>
              </dd>
            </div>
          ))}
        </dl>
        {c.why && (
          <p className="max-w-[62ch] border-l-2 border-border pl-3 text-sm leading-6">
            <span className="mr-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Why</span>
            {c.why}
          </p>
        )}
        <Link href={readHref} className="inline-block text-sm font-medium text-primary hover:underline">
          Read this cluster →
        </Link>
      </div>
      <div className="grid w-full grid-cols-3 gap-2 sm:w-56">
        {c.thumbs.map((t) => (
          <Thumb key={t.id} row={t} />
        ))}
      </div>
    </article>
  );
}

export function ClusterCompare({ data, fmt, readHref }: { data: Data; fmt: Formatters; readHref: (key: string) => string }) {
  const tested = data.vs_rest.filter((c) => !c.too_small);
  const quiet = [
    ...(data.level.length ? [`No clear difference: ${data.level.join(', ')}.`] : []),
    ...(data.too_small.length ? [`Too few videos to judge (under 5): ${data.too_small.join(', ')}.`] : []),
  ];
  return (
    <section className="space-y-5 rounded-xl border border-border px-4 py-5" aria-labelledby="compare-title">
      <div className="space-y-1">
        <h2 id="compare-title" className="flex flex-wrap items-center gap-2 text-lg font-semibold">
          Across clusters <ScopeLabel>Last 30 days · $200+ spent</ScopeLabel>
        </h2>
        <p className="max-w-prose text-sm text-muted-foreground">
          Each cluster against the whole account (every video, same days), graded the way the Insights tab grades a pattern. Green or red = the gap holds up;
          black = could be chance. Rates are the typical video; cost per purchase pools every dollar.
        </p>
      </div>
      {data.cards.length ? (
        <div>
          {data.cards.map((c) =>
            c.content?.length ? (
              <ContentCard key={c.key} c={c} name={c.key} readHref={readHref(c.key)} />
            ) : (
              <Card key={c.key} c={c} fmt={fmt} readHref={readHref(c.key)} />
            ),
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No cluster differs from the others by more than chance would give.</p>
      )}
      {quiet.length > 0 && <p className="text-xs text-muted-foreground">{quiet.join(' ')}</p>}
      <details className="group rounded-xl border border-border">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
          <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden="true" />
          Every number behind these
        </summary>
        <div className="space-y-2 border-t border-border px-4 py-4">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="py-2 pr-3 font-medium">Cluster</th>
                  <th className="px-2 py-2 text-right font-medium">Videos</th>
                  <th className="px-2 py-2 text-right font-medium">Its CPA</th>
                  {ORDER.map((k) => (
                    <th key={k} className="px-2 py-2 text-right font-medium">
                      {SHORT[k]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tested.map((c) => {
                  const by = new Map(c.metrics.filter((m): m is ClusterMetric => !!m).map((m) => [m.metric, m]));
                  return (
                    <tr key={c.key} className="border-b border-border/60 last:border-0">
                      <td className="py-2 pr-3 font-medium">{c.name}</td>
                      <td className="px-2 py-2 text-right tabular-nums">{c.totals.videos}</td>
                      <td className="px-2 py-2 text-right tabular-nums">{c.totals.cpa != null ? fmt.money(c.totals.cpa) : '–'}</td>
                      {ORDER.map((k) => {
                        const m = by.get(k);
                        return (
                          <td key={k} className="px-2 py-2 text-right tabular-nums">
                            {m ? (
                              <span
                                className={cn(tone(m), HOLDS.has(m.grade) && 'font-semibold')}
                                title={`${value(m, m.mine, fmt)} vs ${value(m, m.theirs, fmt)} · ${m.grade}`}
                              >
                                {signed(m.change)}
                              </span>
                            ) : (
                              '–'
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground">
            Change against the rest of the account; coloured = strong or suggestive. Stop, ThruPlay and CTR compare the typical video; cost and purchases pool
            every dollar.
          </p>
        </div>
      </details>
    </section>
  );
}
