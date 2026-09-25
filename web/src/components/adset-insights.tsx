import Link from 'next/link';
import { ImageOff, PlayCircle } from 'lucide-react';
import { cn } from 'cn';
import { blobUrl } from '@/lib/blob-url';
import type { AdSetCard, AdSetFormatRead, AdSetRow, AdSetStat } from '@/lib/types';

// The ad set read as a handful of decisions, in the Insights tab's shape: a plain title, the
// two numbers that prove it, one line of why, and the ads beside it. Everything else on the
// read sits folded under these.

const KIND: Record<AdSetCard['kind'], { label: string; tone: string }> = {
  scale: { label: 'Scale', tone: 'bg-success/15 text-success' },
  cut: { label: 'Cut or rework', tone: 'bg-destructive/15 text-destructive' },
  recut: { label: 'Recut opening', tone: 'bg-chart-2/20 text-chart-2' },
  fix_middle: { label: 'Fix the middle', tone: 'bg-chart-2/20 text-chart-2' },
  fix_promise: { label: 'Fix the promise', tone: 'bg-chart-2/20 text-chart-2' },
};

export function Thumb({ row, note }: { row: Pick<AdSetRow, 'creative_id' | 'name' | 'poster_url' | 'duration_s'> | undefined; note?: string }) {
  if (!row) return null;
  const poster = blobUrl(row.poster_url);
  const body = (
    <span className="block space-y-1">
      <span className="relative block aspect-[4/5] overflow-hidden rounded-md bg-muted">
        {poster ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={poster} alt="" loading="lazy" className="h-full w-full object-cover" />
        ) : (
          <ImageOff className="absolute inset-0 m-auto size-4 text-muted-foreground" aria-hidden="true" />
        )}
        {row.duration_s != null && (
          <span className="absolute top-1 right-1 inline-flex items-center gap-0.5 rounded-full bg-black/60 px-1 text-[10px] text-white tabular-nums">
            <PlayCircle className="size-2.5" aria-hidden="true" />
            {Math.round(row.duration_s)}s
          </span>
        )}
      </span>
      {note && <span className="block text-[10px] font-medium text-muted-foreground">{note}</span>}
    </span>
  );
  return row.creative_id ? (
    <Link href={`/creatives/${row.creative_id}`} className="block transition-opacity hover:opacity-80" title={row.name ?? ''}>
      {body}
    </Link>
  ) : (
    body
  );
}

const tone = (good: boolean | null) => (good === true ? 'text-success' : good === false ? 'text-destructive' : 'text-foreground');

// The two comparisons after an ad's number: its group, then the account. Each carries its own
// arrow and colour; "=" means the gap is under 15% or not clear of noise.
function Against({ s, group }: { s: AdSetStat; group: string }) {
  const mark = (good: boolean | null) =>
    good === true ? <span className="text-success">↑</span> : good === false ? <span className="text-destructive">↓</span> : <span>=</span>;
  return (
    <>
      {[
        [group, s.group, s.group_good],
        ['account', s.account, s.account_good],
      ].map(([name, v, good]) => (
        <span key={name as string} className="ml-1.5 text-sm text-muted-foreground">
          · {name as string} {v as string} {mark(good as boolean | null)}
        </span>
      ))}
    </>
  );
}

function Card({ c, rows, group }: { c: AdSetCard; rows: AdSetRow[]; group: string }) {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const k = KIND[c.kind];
  const thumbs: { row: AdSetRow | undefined; note?: string }[] = c.ads
    ? c.ads.slice(0, 3).map((a) => ({ row: byId.get(a.ad) }))
    : [{ row: byId.get(c.ad) }, ...(c.compare_with ? [{ row: byId.get(c.compare_with), note: 'Best opener here' }] : [])];
  return (
    <article className="grid gap-x-6 gap-y-3 border-t border-border py-6 first:border-t-0 first:pt-0 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0 space-y-3">
        <div className="flex items-start gap-2.5">
          <span className={cn('mt-1 shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold', k.tone)}>{k.label}</span>
          <h4 className="text-lg leading-snug font-semibold tracking-tight text-balance [overflow-wrap:anywhere]">{c.title}</h4>
        </div>
        {c.stats.length > 0 && (
          <dl className="flex flex-wrap gap-x-8 gap-y-2">
            {c.stats.map((s) => (
              <div key={s.label}>
                <dt className="text-xs text-muted-foreground">{s.label}</dt>
                <dd className="tabular-nums">
                  <span className={cn('text-base font-semibold', tone(s.group_good))}>{s.mine}</span>
                  <Against s={s} group={group} />
                </dd>
              </div>
            ))}
          </dl>
        )}
        {c.ads && (
          <ul className="space-y-1 text-sm">
            {c.ads.map((a) => (
              <li key={a.ad} className="flex flex-wrap items-baseline gap-x-2">
                <span translate="no" className="font-medium">
                  {a.name}
                </span>
                <span className="text-muted-foreground tabular-nums">
                  {a.label.toLowerCase()} <span className={cn('font-semibold', tone(a.group_good))}>{a.mine}</span>
                  <Against s={a} group={group} />
                </span>
              </li>
            ))}
          </ul>
        )}
        {c.why && (
          <p className="max-w-[62ch] border-l-2 border-border pl-3 text-sm leading-6">
            <span className="mr-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Why</span>
            {c.why}
          </p>
        )}
      </div>
      <div className="grid w-full grid-cols-3 gap-2 sm:w-56">
        {thumbs.map((t, i) => (
          <Thumb key={t.row?.id ?? i} row={t.row} note={t.note} />
        ))}
      </div>
    </article>
  );
}

export function AdSetInsights({ f, group = 'ad set' }: { f: AdSetFormatRead; group?: string }) {
  const h = f.headline;
  return (
    <div className="space-y-5">
      {h && (
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 rounded-xl bg-muted/50 px-4 py-3">
          <p className="text-sm font-semibold">{h.title}</p>
          {h.stats.map((s) => (
            <p key={s.label} className="text-sm text-muted-foreground tabular-nums">
              {s.label}{' '}
              <span className={cn('font-semibold', s.status === 'strong' ? 'text-success' : s.status === 'weak' ? 'text-destructive' : 'text-foreground')}>
                {s.mine}
              </span>{' '}
              vs {s.theirs}
            </p>
          ))}
        </div>
      )}
      {f.cards.length ? (
        <div>
          {f.cards.map((c) => (
            <Card key={c.ad + c.kind} c={c} rows={f.rows} group={group} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          No ad here differs clearly enough from the rest to act on. Most of this ad set is too thin or too level to call.
        </p>
      )}
    </div>
  );
}
