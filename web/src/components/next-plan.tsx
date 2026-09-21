import Link from 'next/link';
import { ArrowUpRight, Play } from 'lucide-react';
import { cn } from 'cn';
import { Badge } from '@/components/ui/badge';
import type { NextAllocationRow, NextBrief, NextDecline, NextEvidence, NextStake } from '@/lib/types';
import type { AllocationKind, ExploreGroup, NextView } from '@/lib/next-view';
import { words } from '@/lib/next-view';
import { formatters, type Formatters } from '@/lib/format';

// Type system, inherited from the diagnosis panel so the two pages read as one product:
// 24px for a figure, 16px for a name, 14px for everything else. Hierarchy comes from weight,
// never from shrinking text, and muted-foreground is only used at 14px and up. Colour is
// reserved for status -- a decision's kind *is* its status here, so fix / replace / scale map
// onto the same destructive / chart-2 / success tokens the diagnosis tiers already use, and
// nothing else on the page carries colour.

type KindStyle = { pill: string; rail: string; edge: string; noun: string };

const KIND: Record<AllocationKind, KindStyle> = {
  fix: {
    pill: 'bg-destructive/10 text-destructive',
    rail: 'bg-destructive text-primary-foreground',
    edge: 'border-l-destructive',
    noun: 'Broken',
  },
  replace: {
    pill: 'bg-chart-2/15 text-chart-2',
    rail: 'bg-chart-2 text-primary-foreground',
    edge: 'border-l-chart-2',
    noun: 'Declining',
  },
  scale: {
    pill: 'bg-success/10 text-success',
    rail: 'bg-success text-primary-foreground',
    edge: 'border-l-success',
    noun: 'Under-funded',
  },
};

const signed = (x: number) => `${x >= 0 ? '+' : '−'}${Math.round(Math.abs(x) * 100)}%`;
const fmtVal = (m: string, v: number, f: Formatters) =>
  m === 'cpa' ? f.money(v) : m === 'purchases_per_1k' ? v.toFixed(2) : `${(100 * v).toFixed(1)}%`;
const fmtDay = (iso: string | null) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) : '';

// Evidence is signed so that positive always means better; a cost that improved went down.
const evidenceTone = (e: NextEvidence) =>
  e.metric === 'cpa'
    ? e.change <= -0.15
      ? 'text-success'
      : 'text-muted-foreground'
    : e.change >= 0.15
      ? 'text-success'
      : 'text-muted-foreground';

// The two windows are not the same money and must never read as one number (see
// cs.insights.next_plan.types): an allocation row is the trailing thirty days, a brief is the
// whole read window.
function declineWords(d: NextDecline): string {
  if (d.metric === 'cpa') return d.capped ? 'CPA 5×+' : `CPA ${d.ratio.toFixed(1)}×`;
  return `CTR −${Math.round((1 - d.ratio) * 100)}%`;
}

const windowWords = (s: NextStake) => (s.window === '30d' ? 'in the last 30 days' : 'of read-window spend');

function Stake({ stake, f }: { stake: NextStake; f: Formatters }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
      <span className="text-2xl leading-none font-semibold tabular-nums">{f.money(stake.amount)}</span>
      <span className="text-sm text-muted-foreground">{windowWords(stake)}</span>
      <span className="text-sm font-semibold text-muted-foreground tabular-nums">{Math.round(stake.share * 100)}%</span>
    </div>
  );
}

function Poster({ url, className }: { url?: string | null; className?: string }) {
  return (
    <div className={cn('relative shrink-0 overflow-hidden rounded-md bg-muted', className)}>
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover" />
      ) : (
        <Play className="absolute inset-0 m-auto size-4 text-muted-foreground/50" aria-hidden="true" />
      )}
    </div>
  );
}

// The account's answer in one band, the way the creative page opens with its metric strip.
// Google Ads leads its recommendations with a single score for the same reason: a page of
// cards needs one number that says how much of the account is actually in play.
function Summary({ view, f }: { view: NextView; f: Formatters }) {
  const rows = view.groups.flatMap((g) => g.rows);
  const total = rows.reduce((s, r) => s + r.stake.amount, 0);
  const share = rows.reduce((s, r) => s + r.stake.share, 0);
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="grid divide-y divide-border sm:grid-cols-4 sm:divide-x sm:divide-y-0">
        <div className="flex flex-col gap-1.5 p-4">
          <span className="text-sm text-muted-foreground">Under a decision</span>
          <span className="text-2xl leading-none font-semibold tabular-nums">{f.money(total)}</span>
          <span className="text-sm text-muted-foreground tabular-nums">{Math.round(share * 100)}% of the last 30 days</span>
        </div>
        {(['fix', 'replace', 'scale'] as const).map((kind) => {
          const g = view.groups.find((x) => x.kind === kind);
          const n = g?.rows.length ?? 0;
          const sum = g?.rows.reduce((s, r) => s + r.stake.amount, 0) ?? 0;
          return (
            <div key={kind} className="flex flex-col gap-1.5 p-4">
              <span className="text-sm text-muted-foreground">{KIND[kind].noun}</span>
              <span className={cn('text-2xl leading-none font-semibold tabular-nums', n === 0 && 'text-muted-foreground')}>{n || '—'}</span>
              <span className="text-sm text-muted-foreground tabular-nums">{n ? `${n === 1 ? 'ad' : 'ads'} · ${f.money(sum)}` : 'none'}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SectionNav({ view }: { view: NextView }) {
  const items: [string, string][] = [
    ['decide', 'Decide'],
    ['make', 'Make'],
    ['explore', 'Explore'],
    ...(view.nextTests.length ? ([['read', 'From the read']] as [string, string][]) : []),
  ];
  return (
    <nav aria-label="Sections" className="sticky top-0 z-10 -mx-1 flex gap-1 overflow-x-auto bg-background/90 px-1 py-2 text-sm backdrop-blur">
      {items.map(([id, label]) => (
        <a
          key={id}
          href={`#${id}`}
          className="rounded-md px-2.5 py-1 whitespace-nowrap text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {label}
        </a>
      ))}
    </nav>
  );
}

function DecisionCard({ row, kind, rank, f }: { row: NextAllocationRow; kind: AllocationKind; rank: number; f: Formatters }) {
  const s = KIND[kind];
  return (
    <Link
      href={`/creatives/${row.creative_id}`}
      className={cn(
        'group flex gap-4 rounded-xl border border-l-[3px] border-border bg-card p-4 transition-colors hover:bg-accent/40',
        'focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none',
        s.edge,
      )}
    >
      <Poster url={row.poster_url} className="aspect-[9/16] w-16" />
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex items-start gap-2.5">
          <span className={cn('mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full text-sm font-semibold tabular-nums', s.rail)}>
            {rank}
          </span>
          <span translate="no" className="min-w-0 flex-1 truncate text-base font-medium text-foreground">
            {row.headline}
          </span>
          {(row.broken_layer || row.decline) && (
            <span
              className={cn('inline-flex h-6 shrink-0 items-center rounded-4xl px-2.5 text-sm font-medium tabular-nums', s.pill)}
              title={row.decline ? `First 14 days ${row.decline.baseline.toFixed(2)} → trailing 7 days ${row.decline.observed.toFixed(2)}` : undefined}
            >
              {row.broken_layer ?? declineWords(row.decline!)}
            </span>
          )}
          <ArrowUpRight
            className="mt-0.5 size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
            aria-hidden="true"
          />
        </div>
        <Stake stake={row.stake} f={f} />
        <div>
          <p className="text-sm leading-6 text-foreground">{row.verdict}</p>
          {row.detail && <p className="text-sm leading-6 text-muted-foreground">{row.detail}</p>}
        </div>
      </div>
    </Link>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-x-4 gap-y-1 py-3 sm:grid-cols-[7rem_minmax(0,1fr)]">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-sm leading-6 text-foreground">{children}</dd>
    </div>
  );
}

function BriefCard({ brief, rank, f }: { brief: NextBrief; rank: number; f: Formatters }) {
  const s = brief.shape;
  const shape = [
    // The range is the prescription; the median only says where the nearest files sit in it.
    s.duration_range
      ? `${s.duration_range[0].toFixed(0)}–${s.duration_range[1].toFixed(0)}s${s.duration_s ? ` (nearest files ~${s.duration_s.toFixed(0)}s)` : ''}`
      : null,
    s.first_product_s != null ? `product on screen by ${s.first_product_s.toFixed(0)}s` : null,
    s.captions ? `captions: ${words(s.captions)}` : null,
  ].filter(Boolean);

  return (
    <article className="rounded-xl border border-border bg-card">
      <header className="flex flex-col gap-3 border-b border-border p-4">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-semibold tabular-nums text-muted-foreground">
            {rank}
          </span>
          <h3 className="min-w-0 flex-1 text-base font-medium text-balance text-foreground">{brief.title}</h3>
          {brief.carriers === 0 && (
            <Badge variant="outline" className="shrink-0">
              Never shot
            </Badge>
          )}
        </div>
        <Stake stake={brief.stake} f={f} />
        <p className="text-sm text-muted-foreground">Held back by {brief.binding}</p>
      </header>

      <dl className="divide-y divide-border px-4">
        <Row label="Evidence">
          <ul className="flex flex-col gap-2">
            {brief.evidence.map((e) => (
              <li key={e.pattern_id} className="flex flex-wrap items-baseline gap-x-2">
                <span className={cn('w-12 shrink-0 text-sm font-semibold tabular-nums', evidenceTone(e))}>{signed(e.change)}</span>
                <span className="font-medium">{e.label}</span>
                <span className="text-muted-foreground tabular-nums">
                  on {e.metric_name} · {fmtVal(e.metric, e.cell_value, f)} vs {fmtVal(e.metric, e.comp_value, f)} · {e.n_cell} of{' '}
                  {e.n_cell + e.n_complement} files · {e.grade}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-muted-foreground">{brief.downstream}</p>
        </Row>

        {brief.hooks.length > 0 && (
          <Row label="Hooks">
            <p className="text-muted-foreground">Verbatim, from the files that carry each ingredient. Write against them, do not reuse them.</p>
            <ul className="mt-2 flex flex-col gap-2">
              {brief.hooks.map((h, i) => (
                <li key={i} className="border-l-2 border-border pl-3 text-balance italic">
                  &ldquo;{h}&rdquo;
                </li>
              ))}
            </ul>
          </Row>
        )}

        {brief.refs.length > 0 && (
          <Row label="Reference">
            <ul className="flex flex-wrap gap-3">
              {brief.refs.map((r) => (
                <li key={r.sha256}>
                  <Link
                    href={r.rep_creative_id ? `/creatives/${r.rep_creative_id}` : '/creatives'}
                    className="flex w-44 gap-2.5 rounded-lg border border-border p-2 transition-colors hover:bg-accent/40 focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <Poster url={r.poster_url} className="aspect-[9/16] w-10" />
                    <span className="flex min-w-0 flex-col gap-1">
                      <span className="line-clamp-2 text-sm leading-5">{r.headline}</span>
                      <span className="text-sm text-muted-foreground tabular-nums">{f.money(r.spend)}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </Row>
        )}

        {shape.length > 0 && (
          <Row label="Shape">
            {shape.join(' · ')} <span className="text-muted-foreground">— from the {s.n_carriers} files nearest this combination</span>
          </Row>
        )}
        <Row label="Hold">{brief.hold}</Row>
        <Row label="Decide on">
          <span className="font-medium">{brief.watch.metric_name}</span> must come {brief.watch.direction}{' '}
          <span className="font-semibold tabular-nums">{fmtVal(brief.watch.metric, brief.watch.account_value, f)}</span>{' '}
          <span className="text-muted-foreground">({brief.watch.account_label})</span>
        </Row>
        {brief.caveat && (
          <Row label="Caveat">
            <span className="text-muted-foreground">{brief.caveat}</span>
          </Row>
        )}
      </dl>
    </article>
  );
}

function ExploreRow({ group }: { group: ExploreGroup }) {
  return (
    <div className="grid gap-x-4 gap-y-2 py-3 sm:grid-cols-[10rem_minmax(0,1fr)]">
      <span className="text-sm text-muted-foreground">{group.label}</span>
      <div className="flex flex-wrap gap-1.5">
        {group.never.map((r) => (
          <span key={r.key} className="inline-flex h-6 items-center rounded-md border border-border px-2 text-sm">
            {words(r.value)}
          </span>
        ))}
        {group.thin.map((r) => (
          <span
            key={r.key}
            className="inline-flex h-6 items-center gap-1.5 rounded-md border border-dashed border-border px-2 text-sm text-muted-foreground"
            title={`${r.n_units} files — too few to compare`}
          >
            {words(r.value)}
            <span className="tabular-nums">{r.n_units}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Section({
  id,
  title,
  blurb,
  count,
  children,
}: {
  id: string;
  title: string;
  blurb: string;
  count?: number | null;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-14 space-y-4">
      <div className="max-w-[70ch] space-y-1">
        <div className="flex items-baseline gap-2">
          <h2 className="text-xl font-semibold tracking-tight text-foreground">{title}</h2>
          {count != null && <span className="text-sm text-muted-foreground tabular-nums">{count}</span>}
        </div>
        <p className="text-sm leading-6 text-pretty text-muted-foreground">{blurb}</p>
      </div>
      {children}
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">{children}</p>;
}

export function NextPlanSection({ view, currency }: { view: NextView; currency?: string }) {
  const f = formatters(currency);
  const { basis } = view;
  const exploreCount = view.explore.reduce((n, g) => n + g.never.length + g.thin.length, 0);

  return (
    <div className="max-w-5xl space-y-6">
      {basis.dataThrough && (
        <p className="text-sm text-muted-foreground tabular-nums">Data through {fmtDay(basis.dataThrough)}</p>
      )}
      {!view.allocateEmpty && <Summary view={view} f={f} />}
      <SectionNav view={view} />

      <div className="space-y-14">
        <Section
          id="decide"
          count={view.groups.reduce((n, g) => n + g.rows.length, 0) || null}
          title="Decide"
          blurb="What to do with the ads already running. Ranked inside each decision, never across them — a scale candidate is small precisely because it has not been scaled yet."
        >
          {view.allocateEmpty ? (
            <Empty>{view.allocateEmpty}</Empty>
          ) : (
            <div className="space-y-8">
              {view.groups.map((g) => (
                <div key={g.kind} className="space-y-3">
                  <div className="flex flex-wrap items-baseline gap-x-2.5">
                    <h3 className="text-base font-medium text-foreground">{g.label}</h3>
                    <span className="text-sm text-muted-foreground tabular-nums">
                      {g.rows.length} {g.rows.length === 1 ? 'ad' : 'ads'} · {f.money(g.rows.reduce((s, r) => s + r.stake.amount, 0))}
                    </span>
                    <span className="text-sm text-muted-foreground">{g.blurb}</span>
                  </div>
                  <div className="grid gap-3">
                    {g.rows.map((r, i) => (
                      <DecisionCard key={r.key} row={r} kind={g.kind} rank={i + 1} f={f} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section
          id="make"
          count={view.briefs?.length || null}
          title="Make"
          blurb="Ingredients this account has evidence for and has never shot together. Every number here is measured; none of it is a forecast."
        >
          {view.makeEmpty ? (
            <Empty>{view.makeEmpty}</Empty>
          ) : (
            <div className="grid gap-4">
              {view.briefs?.map((b, i) => (
                <BriefCard key={b.key} brief={b} rank={i + 1} f={f} />
              ))}
            </div>
          )}
        </Section>

        <Section
          id="explore"
          count={exploreCount || null}
          title="Explore"
          blurb="Values the tagger knows that this account has never run, or has run too few times to compare. Untested is not unpromising — there is no evidence either way, so nothing here is ranked."
        >
          {view.exploreEmpty ? (
            <Empty>{view.exploreEmpty}</Empty>
          ) : (
            <div className="space-y-2">
              {/* Two chip styles carry the whole distinction, so the page has to name them. */}
              <p className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <span className="inline-flex h-5 items-center rounded-md border border-border px-1.5 text-sm text-foreground">solid</span>
                  never run
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="inline-flex h-5 items-center gap-1 rounded-md border border-dashed border-border px-1.5 text-sm">
                    dashed <span className="tabular-nums">n</span>
                  </span>
                  run on n files, under the five needed to compare
                </span>
              </p>
              <div className="divide-y divide-border rounded-xl border border-border bg-card px-4 py-1">
                {view.explore.map((g) => (
                  <ExploreRow key={g.dimension} group={g} />
                ))}
              </div>
            </div>
          )}
        </Section>

        {view.nextTests.length > 0 && (
          <Section
            id="read"
            count={view.nextTests.length}
            title="From the read"
            blurb="Tests the account read proposed in prose, kept here so every recommendation lives in one place."
          >
            <ol className="divide-y divide-border rounded-xl border border-border bg-card px-4">
              {view.nextTests.map((t, i) => (
                <li key={i} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-2 py-3 text-sm leading-6">
                  <span className="text-muted-foreground tabular-nums">{i + 1}</span>
                  <span className="text-pretty">{t}</span>
                </li>
              ))}
            </ol>
          </Section>
        )}
      </div>

      <p className="border-t border-border pt-4 text-sm text-muted-foreground tabular-nums">
        {basis.units} files · {f.money(basis.spend)} over {fmtDay(basis.firstDay)}–{fmtDay(basis.lastDay)}
      </p>
    </div>
  );
}
