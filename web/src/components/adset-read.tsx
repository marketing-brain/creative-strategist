import Link from 'next/link';
import { ArrowDown, ArrowUp, ChevronRight, ImageOff, Minus } from 'lucide-react';
import { cn } from 'cn';
import { AdWhy } from '@/components/ad-why';
import { AdSetInsights } from '@/components/adset-insights';
import { DiagnosisPanel } from '@/components/diagnosis-panel';
import { label, words } from '@/components/tag-list';
import { WatchThroughChart } from '@/components/watch-through-chart';
import { blobUrl } from '@/lib/blob-url';
import type { Formatters } from '@/lib/format';
import type { AdSetFormatRead, AdSetRead, AdSetRow, RangedRate, SiblingVerdict } from '@/lib/types';

const day = (iso: string | null) =>
  iso ? new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }) : '?';

type Col = { key: 'hook_rate' | 'hold_rate' | 'link_ctr' | 'cpa'; label: string; kind: 'pct' | 'money'; video?: boolean; hint: string };
const COLS: Col[] = [
  { key: 'hook_rate', label: 'Hook rate', kind: 'pct', video: true, hint: '3-second views per impression' },
  {
    key: 'hold_rate',
    label: 'Hold',
    kind: 'pct',
    video: true,
    hint: 'ThruPlays per 3-second view. A ThruPlay is 15 seconds or the whole video, so short videos hold higher',
  },
  { key: 'link_ctr', label: 'Link CTR', kind: 'pct', hint: 'Link clicks per impression' },
  { key: 'cpa', label: 'Cost / purchase', kind: 'money', hint: 'Spend per purchase' },
];

const VERDICT: Record<Exclude<SiblingVerdict, null>, { text: string; tone: string; Icon: typeof ArrowUp }> = {
  better: { text: 'Better', tone: 'text-success', Icon: ArrowUp },
  worse: { text: 'Worse', tone: 'text-destructive', Icon: ArrowDown },
  level: { text: 'Level', tone: 'text-muted-foreground', Icon: Minus },
};

// One comparison, labeled with what it is against. No verdict means one side is too thin.
function Against({ who, v, other, show }: { who: string; v: SiblingVerdict; other: RangedRate | undefined; show: (n: number | null) => string }) {
  const V = v ? VERDICT[v] : null;
  return (
    <span
      className="flex items-center justify-end gap-1 text-[11px] whitespace-nowrap"
      title={other?.readable ? `${who}: ${show(other.value ?? null)}` : `${who}: too thin to compare`}
    >
      <span className="text-muted-foreground">{who}</span>
      {V ? (
        <span className={cn('inline-flex items-center gap-0.5 font-semibold', V.tone)}>
          <V.Icon className="size-3" aria-hidden="true" />
          {V.text}
        </span>
      ) : (
        <span className="text-muted-foreground/70">n/a</span>
      )}
    </span>
  );
}

function Cell({ r, row, k, col, fmt, group }: { r: RangedRate; row: AdSetRow; k: Col['key']; col: Col; fmt: Formatters; group: string }) {
  if (!r.readable || r.value == null) return <span className="text-xs text-muted-foreground">too thin</span>;
  const show = (n: number | null) => (n == null ? '–' : col.kind === 'pct' ? fmt.pct(n, 1) : fmt.money(n));
  return (
    <span className="inline-flex flex-col items-end gap-0.5" title={`95% range ${show(r.low)} – ${r.high == null ? '∞' : show(r.high)}`}>
      <span className="font-medium tabular-nums">{show(r.value)}</span>
      <span className="text-[11px] text-muted-foreground tabular-nums">
        {show(r.low)}–{r.high == null ? '∞' : show(r.high)}
      </span>
      <Against who={group.charAt(0).toUpperCase() + group.slice(1)} v={row.vs_siblings[k]} other={row.siblings[k]} show={show} />
      <Against who="Account" v={row.vs_account[k]} other={row.account?.[k]} show={show} />
    </span>
  );
}

// A file's standing in one line per comparison: how many measured layers it wins and loses.
function Standing({ row, keys, group }: { row: AdSetRow; keys: Col['key'][]; group: string }) {
  const line = (who: string, vs: AdSetRow['vs_siblings']) => {
    const got = keys.map((k) => vs[k]).filter(Boolean);
    if (!got.length) return `${who}: too thin to compare`;
    const n = (x: SiblingVerdict) => got.filter((v) => v === x).length;
    const parts = [n('better') && `${n('better')} better`, n('worse') && `${n('worse')} worse`, n('level') && `${n('level')} level`].filter(Boolean);
    return `${who}: ${parts.join(', ')}`;
  };
  return (
    <span className="mt-1.5 block space-y-0.5 text-[11px] text-muted-foreground">
      <span className="block">{line(`vs ${group}`, row.vs_siblings)}</span>
      <span className="block">{line('vs account', row.vs_account)}</span>
    </span>
  );
}

function FileName({ row }: { row: AdSetRow }) {
  const poster = blobUrl(row.poster_url);
  const body = (
    <span className="flex items-center gap-2.5">
      <span className="relative size-10 shrink-0 overflow-hidden rounded bg-muted">
        {poster ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={poster} alt="" loading="lazy" className="h-full w-full object-cover" />
        ) : (
          <ImageOff className="m-auto mt-3 size-4 text-muted-foreground" aria-hidden="true" />
        )}
      </span>
      <span className="min-w-0">
        <span translate="no" className="line-clamp-2 text-xs font-medium [overflow-wrap:anywhere]">
          {row.name ?? 'Untitled'}
        </span>
        <span className="text-[11px] text-muted-foreground tabular-nums">
          {row.duration_s != null ? `${Math.round(row.duration_s)}s · ` : ''}
          {row.days} day{row.days === 1 ? '' : 's'}
        </span>
      </span>
    </span>
  );
  return row.creative_id ? (
    <Link href={`/creatives/${row.creative_id}`} className="block hover:underline">
      {body}
    </Link>
  ) : (
    body
  );
}

function FormatRead({ f, fmt, group, tableOnly = false }: { f: AdSetFormatRead; fmt: Formatters; group: string; tableOnly?: boolean }) {
  const noun = f.format === 'video' ? 'videos' : 'images';
  const cols = COLS.filter((c) => !c.video || f.format === 'video');
  const best = f.best ? f.rows.find((r) => r.id === f.best!.id) : undefined;
  const d = f.diagnosis;
  return (
    <div className="space-y-8">
      <p className="text-sm text-muted-foreground">
        {f.files} {noun}, {f.delivered} with delivery in this {group}, {day(f.since)} – {day(f.until)}.
      </p>

      {!tableOnly && (
        <div className="space-y-2">
          <h3 className="font-semibold">1 · The {group} against the account</h3>
          {d ? (
            <DiagnosisPanel
              diagnosis={d}
              title={`All ${f.files} ${noun} pooled, ${day(f.since)} – ${day(f.until)}`}
              compare={`Against ${d.baseline_creatives} other ${noun} in the account over the same days`}
              footnote={`The same five-layer read as the creative page, with the ${group} standing in for one ad. Retention is read file by file below: videos of different lengths can't share one curve.`}
            />
          ) : (
            <p className="text-sm text-muted-foreground">No delivery to read.</p>
          )}
        </div>
      )}

      <div className="space-y-2">
        {!tableOnly && (
          <h3 className="font-semibold">
            2 · Each of the {noun}, against its {group} and against the account
          </h3>
        )}
        <p className="max-w-prose text-sm text-muted-foreground">
          Every number is compared twice. <span className="font-medium text-foreground capitalize">{group}</span>:{' '}
          {group === 'ad set'
            ? 'the other files in this ad set, same audience, budget and dates, so a gap is about the creative. '
            : 'the other videos in this cluster, the same kind of creative; they ran in different ad sets, so a gap is partly the setup. '}
          <span className="font-medium text-foreground">Account</span>: every other {f.format} in the account over the days this file ran here. Under each
          number is its likely range; better or worse is called only when that range clears the other side and the gap is at least 15%. Hover a verdict for the
          number it was compared with.
          {f.format === 'video' &&
            ' Hold favours short videos: a ThruPlay is 15 seconds or the whole video, whichever is shorter, so compare hold between videos of similar length.'}
        </p>
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[40rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">File</th>
                <th className="px-3 py-2 text-right font-medium">Spend</th>
                {cols.map((c) => (
                  <th key={c.key} className="px-3 py-2 text-right font-medium" title={c.hint}>
                    {c.label}
                  </th>
                ))}
                <th className="px-3 py-2 text-right font-medium">ROAS</th>
              </tr>
            </thead>
            <tbody>
              {f.rows.map((r) => (
                <tr key={r.id} className={cn('border-b border-border align-top last:border-0', r.id === f.best?.id && 'bg-chart-1/[0.06]')}>
                  <td className="max-w-72 px-3 py-2.5">
                    <FileName row={r} />
                    <Standing row={r} keys={cols.map((c) => c.key)} group={group} />
                    {r.id === f.best?.id && (
                      <span className="mt-1 inline-block rounded bg-chart-1/15 px-1.5 py-0.5 text-[10px] font-semibold text-chart-1">Best file</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{fmt.money(r.totals.spend)}</td>
                  {cols.map((c) => (
                    <td key={c.key} className="px-3 py-2.5 text-right">
                      <Cell r={r.totals[c.key]} row={r} k={c.key} col={c} fmt={fmt} group={group} />
                    </td>
                  ))}
                  <td className="px-3 py-2.5 text-right tabular-nums">
                    {r.totals.roas.readable && r.totals.roas.value != null ? (
                      `${r.totals.roas.value.toFixed(2)}x`
                    ) : (
                      <span className="text-xs text-muted-foreground">too thin</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {!tableOnly && <AdWhy rows={f.rows} fmt={fmt} noun={noun} group={group} />}

      {!tableOnly && (
        <div className="space-y-3">
          <h3 className="font-semibold">3 · What the best file does differently</h3>
          {!f.best || !best ? (
            <p className="max-w-prose text-sm text-muted-foreground">
              Not enough delivery to pick a best file: it needs two files with 100+ link clicks to rank on cost, or two with 1,000+ impressions to rank on hook
              rate.
            </p>
          ) : (
            <>
              <p className="max-w-prose text-sm">
                <span translate="no" className="font-medium">
                  {best.name}
                </span>{' '}
                <span className="text-muted-foreground">
                  is the best file here by {f.best.basis}, compared with {f.best.compared_with} other tagged file{f.best.compared_with === 1 ? '' : 's'}.
                </span>
              </p>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <h4 className="text-xs font-medium text-muted-foreground">It has, and most of the others don&apos;t</h4>
                  {f.best.has.length ? (
                    <ul className="flex flex-wrap gap-1.5">
                      {f.best.has.map((a) => (
                        <li
                          key={a.dimension + a.value}
                          className="rounded-md bg-success/10 px-2 py-0.5 text-xs"
                          title={`${a.others} of ${f.best!.compared_with} others have it`}
                        >
                          <span className="text-muted-foreground">{label(a.dimension)}:</span> {words(a.value)}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-muted-foreground">Nothing most of the others lack.</p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <h4 className="text-xs font-medium text-muted-foreground">Most of the others have, and it doesn&apos;t</h4>
                  {f.best.lacks.length ? (
                    <ul className="flex flex-wrap gap-1.5">
                      {f.best.lacks.map((a) => (
                        <li
                          key={a.dimension + a.value}
                          className="rounded-md bg-muted px-2 py-0.5 text-xs"
                          title={`${a.others} of ${f.best!.compared_with} others have it`}
                        >
                          <span className="text-muted-foreground">{label(a.dimension)}:</span> {words(a.value)}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-muted-foreground">Nothing most of the others share that it lacks.</p>
                  )}
                </div>
              </div>
              {f.best.described && (
                <dl className="grid max-w-prose gap-2 rounded-lg bg-muted/40 px-4 py-3 text-sm">
                  <p className="text-xs text-muted-foreground">How the model described it in experiment 1 (its own words, not a measurement)</p>
                  {(['hook', 'angle', 'format'] as const).map((k) =>
                    f.best!.described![k] ? (
                      <div key={k}>
                        <dt className="text-xs font-medium text-muted-foreground capitalize">{k}</dt>
                        <dd>{f.best!.described![k]}</dd>
                      </div>
                    ) : null,
                  )}
                </dl>
              )}
              {f.format === 'video' && <WatchThroughChart rows={f.rows} bestId={f.best.id} />}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// `tableOnly`: the numbers fold keeps just the per-file table (the cluster read, where the cards
// above already say what the diagnosis, the whys and the best file would).
export function AdSetReadView({ read, fmt, tableOnly = false }: { read: AdSetRead; fmt: Formatters; tableOnly?: boolean }) {
  const group = read.noun ?? 'ad set';
  return (
    <div className="space-y-10">
      {read.formats.map((f) => (
        <div key={f.format} className="space-y-5">
          {read.formats.length > 1 && <h3 className="text-base font-semibold">{f.format === 'video' ? 'Videos' : 'Images'}</h3>}
          <AdSetInsights f={f} group={group} />
          {/* Everything the cards stand on, for whoever wants to check them. */}
          <details className="group rounded-xl border border-border">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
              <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden="true" />
              Every number behind these
            </summary>
            <div className="border-t border-border px-4 py-5">
              <FormatRead f={f} fmt={fmt} group={group} tableOnly={tableOnly} />
            </div>
          </details>
        </div>
      ))}
    </div>
  );
}
