import Link from 'next/link';
import { ArrowUpRight, ChevronRight, Play } from 'lucide-react';
import { cn } from 'cn';
import { Badge } from '@/components/ui/badge';
import type { NextCard } from '@/lib/types';
import type { Action, ActionGroup, ActionKind, NextView } from '@/lib/next-view';
import { formatters, type Formatters } from '@/lib/format';

// One card for every row on this page. The page used to render creative changes as poster
// comparisons and budget calls as coloured rails with rank badges -- two visual languages for
// one sentence ("here is an ad, here is what to do with it, here is what it is worth"),
// because the two generators were built a day apart. Colour is reserved for the verb and for
// the one comparison that carries a judgement; nothing else on the page is coloured.

const VERB: Record<ActionKind, { label: string; chip: string }> = {
  change: { label: 'Change', chip: 'border-foreground/25 text-foreground' },
  fix: { label: 'Fix', chip: 'border-destructive/40 text-destructive' },
  replace: { label: 'Replace', chip: 'border-chart-2/40 text-chart-2' },
  fund: { label: 'Fund', chip: 'border-success/40 text-success' },
};

// Intl rather than a hardcoded 'en-US': the format follows the reader, not the author.
const dayFmt = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
const fmtDay = (iso: string | null) => (iso ? dayFmt.format(new Date(`${iso.slice(0, 10)}T00:00:00Z`)) : '');

function Poster({ url, alt = '', className }: { url?: string | null; alt?: string; className?: string }) {
  // 4:5 is Meta's feed frame: it crops a 9:16 cut far less brutally than a square and does not
  // letterbox a landscape file into a stripe. Explicit dimensions keep the row from jumping
  // as the image arrives.
  return (
    <div className={cn('relative shrink-0 overflow-hidden rounded-lg bg-muted aspect-[4/5]', className)}>
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={url}
          alt={alt}
          width={104}
          height={130}
          loading="lazy"
          decoding="async"
          className="absolute inset-0 size-full object-cover"
        />
      ) : (
        <Play className="absolute inset-0 m-auto size-5 text-muted-foreground/40" aria-hidden="true" />
      )}
    </div>
  );
}

function Summary({ view, f }: { view: NextView; f: Formatters }) {
  return (
    <dl className="grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-3">
      {[
        { t: 'At stake', v: f.money(view.money), s: 'across every action below' },
        { t: 'Actions', v: String(view.count), s: 'this week' },
        { t: 'Data through', v: fmtDay(view.basis.dataThrough), s: 'last full day of delivery' },
      ].map((cell) => (
        <div key={cell.t} className="flex flex-col gap-1.5 bg-card p-4">
          <dt className="text-sm text-muted-foreground">{cell.t}</dt>
          <dd className="text-2xl leading-none font-semibold tabular-nums">{cell.v}</dd>
          <dd className="text-sm text-muted-foreground">{cell.s}</dd>
        </div>
      ))}
    </dl>
  );
}

// The comparison, shown only where one exists: this ad's number beside the ads beating it.
// It needs no reading, which is the point.
function Comparison({ card, f }: { card: NextCard; f: Formatters }) {
  if (!card.refs.length) return null;
  return (
    <div className="flex flex-wrap items-start gap-x-6 gap-y-4 border-t border-border px-4 py-4">
      <figure className="w-[92px]">
        <Poster url={card.poster_url} alt={`Frame from ${card.title}`} className="w-full" />
        <figcaption className="mt-2">
          <span className="block text-base leading-none font-semibold text-destructive tabular-nums">
            {card.own_value_label ?? card.wrong_value}
          </span>
          <span className="mt-1 block text-sm text-muted-foreground">this ad</span>
        </figcaption>
      </figure>
      <ChevronRight className="mt-[46px] size-5 shrink-0 text-muted-foreground/40" aria-hidden="true" />
      <div className="min-w-0">
        <ul className="flex flex-wrap gap-3">
          {card.refs.map((r) => (
            <li key={r.footage_id}>
              <figure className="w-[92px]">
                {r.rep_creative_id ? (
                  <Link
                    href={`/creatives/${r.rep_creative_id}`}
                    title={r.title}
                    className="block rounded-lg motion-safe:transition-opacity hover:opacity-80 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <Poster url={r.poster_url} alt={`Frame from ${r.title}`} className="w-full" />
                  </Link>
                ) : (
                  <Poster url={r.poster_url} alt={`Frame from ${r.title}`} className="w-full" />
                )}
                {/* No name: at this width every file here renders as "MagAshwa…", which
                    identifies nothing. The frame is the identity, the link carries the rest. */}
                <figcaption className="mt-2 text-base leading-none font-semibold text-success tabular-nums">
                  {r.value_label ?? f.money(r.spend_30d)}
                </figcaption>
              </figure>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-sm text-muted-foreground">
          {card.metric_name} · ads on the right side of this finding
        </p>
      </div>
    </div>
  );
}

function Why({ card }: { card: NextCard }) {
  const l = card.lines;
  return (
    <details className="group border-t border-border">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-4 py-2.5 text-sm text-muted-foreground motion-safe:transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
        <ChevronRight
          className="size-4 motion-safe:transition-transform group-open:rotate-90"
          aria-hidden="true"
        />
        <span className="flex-1 text-left">Why we believe this</span>
        <span className="tabular-nums">
          {card.pid} · {card.grade}
        </span>
      </summary>
      <div className="space-y-2 px-4 pb-4 text-sm leading-6 text-muted-foreground">
        {l.finding && <p className="max-w-[65ch] text-pretty font-medium text-foreground">{l.finding}</p>}
        <p className="max-w-[65ch] text-pretty">{l.evidence}</p>
        {(card.written?.cause ?? l.mechanism) && (
          <p className="max-w-[65ch] text-pretty">{card.written?.cause ?? l.mechanism}</p>
        )}
        <p className="max-w-[65ch] text-pretty">{l.money}</p>
        {l.moment && <p className="max-w-[65ch] text-pretty">{l.moment}</p>}
      </div>
    </details>
  );
}

function ActionCard({ action, f }: { action: Action; f: Formatters }) {
  const verb = VERB[action.kind];
  const rb = action.card?.read_back;
  // "Too early" is the absence of news. Printing it for a week teaches the reader to skip the
  // row that will later carry the outcome.
  const news = rb && rb.outcome !== 'too_early' ? rb : null;

  return (
    <article className="overflow-hidden rounded-xl border border-border bg-card">
      <header className="flex items-start gap-4 p-4">
        {/* Omitted when a comparison follows: it shows this same frame, larger, one row down. */}
        {!action.card && (
          <Poster url={action.posterUrl} alt={`Frame from ${action.adName}`} className="w-14" />
        )}
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className={cn('rounded-sm text-xs', verb.chip)}>
              {verb.label}
            </Badge>
            {action.creativeId ? (
              <Link
                href={`/creatives/${action.creativeId}`}
                translate="no"
                title={action.adName}
                className="inline-flex min-w-0 items-center gap-1 text-sm text-muted-foreground motion-safe:transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <span className="truncate">{action.adName}</span>
                <ArrowUpRight className="size-3.5 shrink-0" aria-hidden="true" />
              </Link>
            ) : (
              <span translate="no" className="truncate text-sm text-muted-foreground">
                {action.adName}
              </span>
            )}
          </div>
          <h3 className="text-lg leading-tight font-semibold tracking-tight text-balance">{action.title}</h3>
          <p className="max-w-[65ch] text-sm leading-6 text-pretty text-muted-foreground">{action.detail}</p>
          {action.trade && (
            <p className="max-w-[65ch] rounded-md border-l-2 border-chart-2/50 bg-muted/50 py-1.5 pr-2 pl-3 text-sm leading-6 text-pretty text-muted-foreground">
              {action.trade}
            </p>
          )}
          {action.alsoBudget && (
            <p className="max-w-[65ch] text-sm leading-6 text-pretty text-muted-foreground">
              <span className="font-medium text-foreground">Also a budget call: </span>
              {action.alsoBudget}
            </p>
          )}
        </div>
        <div className="shrink-0 text-right">
          <div className="text-xl leading-none font-semibold tabular-nums">{f.money(action.money)}</div>
          <div className="mt-1 text-sm text-muted-foreground">at stake</div>
        </div>
      </header>

      {action.card && <Comparison card={action.card} f={f} />}
      {action.card && <Why card={action.card} />}

      {news && (
        <p className="border-t border-border bg-muted/40 px-4 py-3 text-sm leading-6 text-pretty text-muted-foreground">
          <span className="font-medium text-foreground">Since this card: </span>
          {news.detail}
          {news.adoption ? ` ${news.adoption}` : ''}
        </p>
      )}
    </article>
  );
}

function Group({ group, empty, f }: { group: ActionGroup; empty: string | null; f: Formatters }) {
  return (
    <section className="space-y-4">
      <div className="max-w-[65ch] space-y-1">
        <div className="flex items-baseline gap-2">
          <h2 className="text-xl font-semibold tracking-tight">{group.title}</h2>
          <span className="text-sm text-muted-foreground tabular-nums">{group.actions.length}</span>
        </div>
        <p className="text-sm leading-6 text-pretty text-muted-foreground">{group.blurb}</p>
      </div>
      {group.actions.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
          {empty ?? 'Nothing here this week.'}
        </p>
      ) : (
        <div className="space-y-3">
          {group.actions.map((a) => (
            <ActionCard key={a.key} action={a} f={f} />
          ))}
        </div>
      )}
    </section>
  );
}

export function NextPlanSection({ view, currency }: { view: NextView; currency?: string }) {
  const f = formatters(currency);
  const { basis } = view;
  return (
    <div className="max-w-4xl space-y-8">
      <Summary view={view} f={f} />
      {view.groups.map((g) => (
        <Group key={g.kind} group={g} empty={g.kind === 'creative' ? view.empty.creative : view.empty.budget} f={f} />
      ))}
      <p className="border-t border-border pt-4 text-sm text-muted-foreground">
        Creative findings come from an account read over{' '}
        <span className="tabular-nums">
          {basis.units} files, {f.money(basis.spend)}, {fmtDay(basis.firstDay)}–{fmtDay(basis.lastDay)}
        </span>
        . Money on this page is the last 30 days.
      </p>
    </div>
  );
}
