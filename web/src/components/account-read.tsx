import Link from 'next/link';
import { ArrowRight, ChevronRight, Play } from 'lucide-react';
import { cn } from 'cn';
import type { AccountReadUnit } from '@/lib/types';
import type { AccountReadView, AttributeRow, FindingView, FunnelCell } from '@/lib/account-read-view';
import { FUNNEL_METRICS } from '@/lib/account-read-view';
import { formatters, type Formatters } from '@/lib/format';
import { Flag, Mark, markClass } from '@/components/account-read-evidence';

const METRIC_LABEL: Record<string, string> = {
  hook_rate: 'Hook rate', thruplay_rate: 'Thruplay', link_ctr: 'Link CTR', purchases_per_1k: 'Purchases / 1k', cpa: 'CPA',
};
const STAGE: Record<string, string> = { funnel: 'Full funnel', attention: 'Hook', hook: 'Hook', retention: 'Hold', click: 'Click', conversion: 'Purchase' };
// What each grade means, in the reader's terms. Thresholds live in cs.insights.account_read.stats.grade.
const GRADE_HELP: Record<string, string> = {
  strong: 'Holds up after correcting for every comparison run',
  suggestive: 'A lead: p < 0.10 on its own, not corrected',
  directional: 'A hint: a gap of 15% or more, weak evidence',
  thin: 'No call: too few files or no real gap',
};

const fmtChange = (x: number) => (Math.abs(x) < 0.005 ? '±0%' : `${x > 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(0)}%`);
const fmtVal = (m: string, v: number, f: Formatters) => (m === 'cpa' ? f.money(v) : m === 'purchases_per_1k' ? v.toFixed(2) : `${(100 * v).toFixed(1)}%`);
const fmtDay = (iso: string | null, year = false) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: year ? 'numeric' : undefined, timeZone: 'UTC' }) : '';

function Cited({ text, citations }: { text: string; citations: Record<string, string> }) {
  // "(P1, P2)" → superscript numbers; the tooltip names the comparison each one points at
  const parts = text.split(/(\s?\((?:P\d+(?:,\s*)?)+\))/g);
  return (
    <>
      {parts.map((p, i) => {
        const ids = /^\s?\(/.test(p) ? p.match(/P\d+/g) : null;
        if (!ids) return <span key={i}>{p}</span>;
        return (
          <sup key={i} className="ml-0.5 font-mono text-[10px] text-muted-foreground">
            {ids.map((id, j) => (
              <span key={id} title={citations[id]}>
                {j > 0 && ','}
                {id.slice(1)}
              </span>
            ))}
          </sup>
        );
      })}
    </>
  );
}

function FunnelCellView({ cell, metric, f }: { cell: FunnelCell | null; metric: string; f: Formatters }) {
  if (!cell) {
    return (
      <div className="flex items-baseline justify-between gap-3 px-3 py-2.5 sm:block sm:py-3">
        <div className="text-[11px] font-medium text-muted-foreground/70 sm:mb-1.5">{METRIC_LABEL[metric]}</div>
        <div className="text-[12px] text-muted-foreground/60">not tested</div>
      </div>
    );
  }
  const good = cell.metric === 'cpa' ? cell.change < 0 : cell.change > 0;
  const called = cell.grade !== 'thin';
  return (
    <div className={cn('flex items-baseline justify-between gap-3 px-3 py-2.5 sm:block sm:py-3', cell.cited && 'bg-muted/60')}>
      <div className="text-[11px] font-medium text-muted-foreground sm:mb-1.5">
        {METRIC_LABEL[cell.metric]}
        {cell.cited && <sup className="ml-0.5 font-mono text-[10px]">{cell.cited.slice(1)}</sup>}
      </div>
      <div className="flex items-baseline gap-2 sm:block">
        <div className={cn('font-mono text-lg font-medium tabular-nums leading-none sm:text-xl', !called ? 'text-muted-foreground/70' : good ? 'text-success' : 'text-destructive')}>
          {fmtChange(cell.change)}
        </div>
        <div className="font-mono text-[11px] tabular-nums text-muted-foreground sm:mt-1.5">
          {fmtVal(cell.metric, cell.cell_value, f)} <span className="text-muted-foreground/60">vs</span> {fmtVal(cell.metric, cell.comp_value, f)}
        </div>
        <Mark className={cn('hidden text-[10px] sm:mt-1 sm:block', markClass[cell.grade] ?? markClass.thin)}>
          <span title={GRADE_HELP[cell.grade]}>{cell.grade === 'thin' ? 'no call' : cell.grade}</span>
        </Mark>
      </div>
    </div>
  );
}

/** One attribute, measured at every step from the first three seconds to the purchase. */
function FunnelStrip({ row, f }: { row: AttributeRow; f: Formatters }) {
  return (
    <figure className="overflow-hidden rounded-lg border border-border">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-3 py-2">
        <span className="text-[13px] font-medium first-letter:uppercase">
          {row.group && <span className="font-normal text-muted-foreground">within {row.group.replace(/_/g, ' ')} · </span>}
          {row.label}
        </span>
        <span className="flex items-center gap-2 font-mono text-[11px] tabular-nums text-muted-foreground">
          {row.n_cell} videos with · {row.n_complement} without
          {row.confounds.map((c) => (
            <Flag key={c}>{c.replace(/_/g, ' ')}</Flag>
          ))}
        </span>
      </figcaption>
      <div className="grid divide-y divide-border sm:grid-cols-5 sm:divide-x sm:divide-y-0">
        {row.cells.map((cell, i) => (
          <FunnelCellView key={FUNNEL_METRICS[i]} cell={cell} metric={FUNNEL_METRICS[i]} f={f} />
        ))}
      </div>
    </figure>
  );
}

function FileCard({ name, unit, f, note }: { name: string; unit: AccountReadUnit | null; f: Formatters; note?: string }) {
  const cpa = unit && unit.purchases > 0 ? unit.spend / unit.purchases : null;
  const body = (
    <>
      <div className="relative aspect-[9/16] w-full overflow-hidden rounded-md bg-muted">
        {unit?.poster_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={unit.poster_url} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
        ) : (
          <Play className="absolute inset-0 m-auto size-4 text-muted-foreground/50" aria-hidden="true" />
        )}
        {note && <span className="absolute left-1 top-1 rounded-sm bg-background/90 px-1 py-px text-[9px] font-semibold uppercase tracking-[0.06em]">{note}</span>}
      </div>
      <div className="mt-1.5 line-clamp-2 text-[12px] font-medium leading-4 text-foreground">{name}</div>
      {unit && (
        <div className="mt-0.5 font-mono text-[11px] tabular-nums text-muted-foreground">
          {f.money(unit.spend)} spend
          {cpa != null && <span className="block">{f.money(cpa)} CPA</span>}
        </div>
      )}
    </>
  );
  const cls = 'group block w-[6.5rem] shrink-0';
  return unit?.rep_creative_id ? (
    <Link href={`/creatives/${unit.rep_creative_id}`} className={cn(cls, 'rounded-md outline-offset-4')}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

function Finding({ v, index, citations, f }: { v: FindingView; index: number; citations: Record<string, string>; f: Formatters }) {
  // Two comparisons carry most findings; the rest fold away.
  const shown = v.attributes.slice(0, 2);
  const rest = v.attributes.slice(2);
  return (
    <article id={`finding-${index + 1}`} className="grid scroll-mt-6 gap-x-8 gap-y-3 border-t border-border py-10 md:grid-cols-[5rem_minmax(0,1fr)]">
      <div className="flex items-baseline gap-3 md:flex-col md:items-start md:gap-1.5">
        <span className="font-mono text-2xl font-medium tabular-nums leading-none text-muted-foreground/50">{String(index + 1).padStart(2, '0')}</span>
        <Mark className="text-muted-foreground">{STAGE[v.layer] ?? v.layer}</Mark>
        <Mark className={markClass[v.confidence] ?? markClass.thin}>
          <span title={GRADE_HELP[v.confidence]}>{v.confidence}</span>
        </Mark>
      </div>
      <div className="min-w-0 max-w-3xl space-y-5">
        <div>
          <h3 className="max-w-[34ch] text-xl font-semibold leading-snug tracking-tight text-balance md:text-[22px]">{v.title}</h3>
          <p className="mt-2 max-w-[62ch] text-[15px] leading-7 text-muted-foreground text-pretty">{v.why_non_obvious}</p>
        </div>

        {shown.map((r) => (
          <FunnelStrip key={r.key} row={r} f={f} />
        ))}
        {rest.length > 0 && (
          <details className="group/more">
            <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-[13px] font-medium text-muted-foreground transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden">
              <ChevronRight className="size-3.5 transition-transform duration-200 group-open/more:rotate-90" aria-hidden="true" />
              {rest.length} more {rest.length === 1 ? 'comparison' : 'comparisons'} behind this
            </summary>
            <div className="mt-3 space-y-3">
              {rest.map((r) => (
                <FunnelStrip key={r.key} row={r} f={f} />
              ))}
            </div>
          </details>
        )}

        <p className="max-w-[68ch] border-l-2 border-border pl-4 text-[15px] leading-7">
          <Mark className="mr-2 text-muted-foreground">Why</Mark>
          {v.mechanism}
        </p>

        <div>
          <Mark className="text-muted-foreground">Watch these</Mark>
          <div className="-mx-1 mt-2 flex gap-3 overflow-x-auto px-1 pb-1">
            {v.exemplars.map((e) => (
              <FileCard key={e.name} name={e.name} unit={e.unit} f={f} />
            ))}
            {v.exception?.unit && <FileCard name={v.exception.name} unit={v.exception.unit} f={f} note="Exception" />}
          </div>
          {/* The writer gives the exception as a sentence more often than as a bare ad name. */}
          {v.exception && !v.exception.unit && (
            <p className="mt-3 max-w-[68ch] text-[14px] leading-6 text-muted-foreground">
              <span className="font-medium text-foreground">Except </span>
              {v.exception.name}
            </p>
          )}
        </div>

        <details className="group/why">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-[13px] font-medium text-muted-foreground transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden">
            <ChevronRight className="size-3.5 transition-transform duration-200 group-open/why:rotate-90" aria-hidden="true" />
            Full reasoning
          </summary>
          <p className="mt-3 max-w-[68ch] text-[15px] leading-7 text-pretty">
            <Cited text={v.paragraph} citations={citations} />
          </p>
        </details>
      </div>
    </article>
  );
}

function GradeBar({ g, tagger }: { g: AccountReadView['grades']; tagger: AccountReadView['tagger'] }) {
  const parts = [
    { k: 'strong', n: g.strong, bar: 'bg-success' },
    { k: 'suggestive', n: g.suggestive, bar: 'bg-foreground/70' },
    { k: 'directional', n: g.directional, bar: 'bg-amber-500/70' },
    { k: 'thin', n: g.thin, bar: 'bg-border' },
  ];
  if (!g.tests) return null;
  return (
    <section aria-label="How much of this is proven" className="max-w-3xl rounded-lg border border-border px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <Mark className="text-muted-foreground">How sure is this</Mark>
        <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{g.tests.toLocaleString()} comparisons run</span>
      </div>
      <div className="mt-3 flex h-1.5 gap-px overflow-hidden rounded-full" aria-hidden="true">
        {parts.map((p) => p.n > 0 && <span key={p.k} className={p.bar} style={{ flexGrow: p.n, minWidth: 4 }} />)}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        {parts.map((p) => (
          <div key={p.k}>
            <dt className="flex items-baseline gap-1.5">
              <span className="font-mono text-base font-medium tabular-nums">{p.n}</span>
              <Mark className={markClass[p.k]}>{p.k === 'thin' ? 'no call' : p.k}</Mark>
            </dt>
            <dd className="mt-0.5 text-[12px] leading-4 text-muted-foreground">{GRADE_HELP[p.k]}</dd>
          </div>
        ))}
      </dl>
      {g.byChance > 0 && (
        <p className="mt-3 border-t border-border pt-3 text-[13px] leading-5 text-muted-foreground">
          With {g.tests.toLocaleString()} comparisons, about {g.byChance} would look suggestive by luck alone. Treat a suggestive finding as a reason to run the test, not as a result.
        </p>
      )}
      {tagger && <TaggerNote t={tagger} />}
    </section>
  );
}

/** What the tags themselves are worth. The tagger watches each video alone, so a file tagged
 *  more than once is a free re-test of its own labels, and the ones it does not reproduce are
 *  still compared here but never pinned on a single ad. */
function TaggerNote({ t }: { t: NonNullable<AccountReadView['tagger']> }) {
  const worst = t.worst.map((w) => `${w.label}, ${Math.round(w.agreement * 100)}% on ${w.files} ${w.files === 1 ? 'video' : 'videos'}`).join('; ');
  const rest = t.flagged > t.worst.length ? `, and ${t.flagged - t.worst.length} more` : '';
  return (
    <p className="mt-3 border-t border-border pt-3 text-[13px] leading-5 text-muted-foreground">
      {`Some files were tagged more than once, which shows how often the tagger repeats its own labels. ${t.flagged} ${t.flagged === 1 ? 'attribute' : 'attributes'} came back under ${Math.round(t.minAgreement * 100)}%. The ones on the most videos: ${worst}${rest}. `}
      Those comparisons still run, marked <Flag>tagger disagreement</Flag>, but no single ad is said to have or lack them.
    </p>
  );
}

function Note({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{title}</h3>
      <div className="max-w-[68ch] text-[14px] leading-6 text-pretty">{children}</div>
    </section>
  );
}

export function AccountReadSection({ status, nUnits, view, stale = false, currency }: {
  status: string; nUnits?: number; view: AccountReadView | null; stale?: boolean; currency?: string;
}) {
  if (status === 'none' || status === 'insufficient') {
    return (
      <section className="border-y border-border py-10 text-center text-sm text-muted-foreground">
        Account read needs 10 tagged video files. This account has {nUnits ?? 0}.
      </section>
    );
  }
  if (!view) {
    return (
      <section className="max-w-[68ch] border-y border-border py-8 text-[15px] leading-7 text-muted-foreground">
        The written read did not pass its checks on the last run, so nothing is shown rather than something unverified. The graded comparisons behind it are current.{' '}
        <Link href="/ask" className="font-medium text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground">
          Put a question to them
        </Link>
        .
      </section>
    );
  }
  const f = formatters(currency);
  const b = view.basis;
  const facts = [`${b.files} videos`, `${b.creatives} ads`, `${f.money(b.spend)} spend`, `${f.count(b.purchases)} purchases`];
  return (
    <div className="max-w-5xl space-y-10">
      <header className="space-y-5">
        <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-xs tabular-nums text-muted-foreground">
          <span>
            {fmtDay(b.first_day)} – {fmtDay(b.last_day, true)}
          </span>
          {facts.map((t) => (
            <span key={t}>
              <span className="mr-3 text-muted-foreground/40">/</span>
              {t}
            </span>
          ))}
        </p>
        <p className="max-w-[40ch] text-2xl font-semibold leading-tight tracking-tight text-balance md:text-[28px] md:leading-[1.2]">
          <Cited text={view.lede} citations={view.citations} />
        </p>
        {view.overviewRest && (
          <p className="max-w-[68ch] text-[15px] leading-7 text-muted-foreground text-pretty">
            <Cited text={view.overviewRest} citations={view.citations} />
          </p>
        )}
        <nav aria-label="On this page" className="flex flex-wrap gap-x-5 gap-y-1 text-[13px] font-medium">
          <a href="#finding-1" className="underline decoration-border underline-offset-4 hover:decoration-foreground">
            {view.findings.length} findings
          </a>
          {view.next_tests.length > 0 && (
            <Link href="/next" className="underline decoration-border underline-offset-4 hover:decoration-foreground">
              {view.next_tests.length} tests to run next
            </Link>
          )}
        </nav>
        {stale && (
          <p className="max-w-[68ch] text-[13px] leading-6 text-amber-700 dark:text-amber-400">
            A newer run did not finish. This is the last read that did{b.created_at ? `, from ${fmtDay(b.created_at, true)}` : ''}.
          </p>
        )}
      </header>

      <GradeBar g={view.grades} tagger={view.tagger} />

      <div>
        {view.findings.map((v, i) => (
          <Finding key={i} v={v} index={i} citations={view.citations} f={f} />
        ))}
      </div>


      <div className="grid gap-x-12 gap-y-8 border-t border-border pt-8 md:grid-cols-2">
        <Note title="Moves together, so can't be told apart">
          <Cited text={view.entangled} citations={view.citations} />
        </Note>
        <Note title="No difference, or too few videos to say">
          <Cited text={view.no_evidence} citations={view.citations} />
        </Note>
        {view.data_needed.length > 0 && (
          <Note title="What would sharpen this">
            <ul className="list-disc space-y-2 pl-5 marker:text-muted-foreground">
              {view.data_needed.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          </Note>
        )}
        <section className="self-start">
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Something this didn&apos;t cover</h3>
          <Link href="/ask" className="group inline-flex items-center gap-1.5 text-[15px] font-medium">
            Put your own question to these comparisons
            <ArrowRight className="size-4 transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden="true" />
          </Link>
        </section>
      </div>
    </div>
  );
}
