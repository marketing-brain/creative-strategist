import Link from 'next/link';
import { ChevronRight, ImageOff } from 'lucide-react';
import { cn } from 'cn';
import { blobUrl } from '@/lib/blob-url';
import type { Formatters } from '@/lib/format';
import type { ThemeGroup, ThemeView } from '@/lib/types';
import { ScopeLabel } from '@/components/scope-label';

// Every axis any themes experiment can be viewed by; each experiment says which it has.
export const THEME_AXES = ['format', 'content', 'funnel', 'funnel_stage', 'duration', 'cluster', 'who', 'animated'] as const;
// Clusters smaller than this fold into one row at the bottom.
const SMALL = 3;

const pct = (x: number | null) => (x == null ? '–' : `${(x * 100).toFixed(1)}%`);

// Videos grouped by one axis: each group's size and pooled numbers over the chosen window,
// then the videos themselves, with the reason for putting each there on hover. Each group links
// to a full read of it, the same read an ad set gets.
export function ThemesView({
  data,
  fmt,
  axis,
  base,
  minSpend = 0,
  window = 'first_7d',
  defaultWindow = 'first_7d',
  fixed = false,
}: {
  data: ThemeView;
  fmt: Formatters;
  axis: string;
  base: string;
  minSpend?: number;
  window?: string;
  defaultWindow?: string;
  // Window and spend fixed by the experiment's rules: no toggles, a label instead.
  fixed?: boolean;
}) {
  const link = (over: { axis?: string; min?: number; window?: string; cluster?: string }) => {
    const q = new URLSearchParams({ axis: over.axis ?? axis });
    const m = over.min ?? minSpend;
    if (m > 0) q.set('min_spend', String(m));
    const w = over.window ?? window;
    if (w !== defaultWindow) q.set('window', w);
    if (over.cluster) q.set('cluster', over.cluster);
    return `${base}?${q}`;
  };
  const day = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const pill = (on: boolean) =>
    cn('rounded-md px-2.5 py-1 font-medium whitespace-nowrap', on ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground');
  const big = data.groups.filter((g) => g.files.length >= SMALL);
  const small = data.groups.filter((g) => g.files.length < SMALL);
  return (
    <section className="space-y-5" aria-labelledby="themes-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        {fixed ? (
          <h2 id="themes-title" className="flex flex-wrap items-center gap-2 text-lg font-semibold">
            The clusters <span className="text-sm font-normal text-muted-foreground tabular-nums">{data.total} videos</span>
            <ScopeLabel>Last 30 days · ${data.min_spend.toLocaleString('en-US')}+ spent</ScopeLabel>
          </h2>
        ) : (
          <h2 id="themes-title" className="text-lg font-semibold">
            {data.total} videos{data.min_spend > 0 && ` with $${data.min_spend.toLocaleString('en-US')}+ lifetime spend`}, grouped by
          </h2>
        )}
        {!fixed && (
          <div className="flex flex-wrap items-center gap-3">
            {data.unlabelled > 0 && <p className="text-xs text-muted-foreground tabular-nums">{data.unlabelled} not sorted yet</p>}
            <div className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5 text-sm" role="group" aria-label="Window">
              {(['last_30d', 'first_7d'] as const).map((w) => (
                <Link key={w} href={link({ window: w })} scroll={false} aria-current={window === w ? 'true' : undefined} className={pill(window === w)}>
                  {w === 'last_30d' ? 'Last 30 days' : 'First 7 days'}
                </Link>
              ))}
            </div>
            <div className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5 text-sm" role="group" aria-label="Minimum lifetime spend">
              {[0, 200, 1000].map((m) => (
                <Link key={m} href={link({ min: m })} scroll={false} aria-current={minSpend === m ? 'true' : undefined} className={pill(minSpend === m)}>
                  {m === 0 ? 'All videos' : `$${m.toLocaleString('en-US')}+ spent`}
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>
      {data.axes.length > 1 && (
        <nav aria-label="Group by" className="-mx-1 flex gap-1 overflow-x-auto border-b border-border px-1">
          {data.axes.map((a) => (
            <Link
              key={a.key}
              href={link({ axis: a.key })}
              scroll={false}
              aria-current={axis === a.key ? 'page' : undefined}
              className={cn(
                '-mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap',
                axis === a.key ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              {a.label}
            </Link>
          ))}
        </nav>
      )}
      <p className="text-xs text-muted-foreground">
        {data.window === 'last_30d' && data.span
          ? `Numbers are the last 30 days (${day(data.span.start)} – ${day(data.span.end)}), pooled across the group.${data.idle ? ` ${data.idle} videos had no delivery in those days and are left out.` : ''}`
          : "Numbers are each video's first 7 days, pooled across the group."}{' '}
        Hover a video to see why it was put there.
      </p>
      <div className="space-y-8">
        {big.map((g) => (
          <Group key={g.key} g={g} fmt={fmt} readHref={link({ cluster: g.key })} />
        ))}
        {small.length > 0 && (
          <details className="group/small rounded-xl border border-border">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
              <ChevronRight className="size-4 transition-transform group-open/small:rotate-90" aria-hidden="true" />
              {small.length} small {small.length === 1 ? 'group' : 'groups'} (fewer than {SMALL} videos each, {small.reduce((n, g) => n + g.files.length, 0)}{' '}
              videos)
            </summary>
            <div className="space-y-8 border-t border-border px-4 py-5">
              {small.map((g) => (
                <Group key={g.key} g={g} fmt={fmt} readHref={link({ cluster: g.key })} />
              ))}
            </div>
          </details>
        )}
      </div>
    </section>
  );
}

function Group({ g, fmt, readHref }: { g: ThemeGroup; fmt: Formatters; readHref: string }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
        <h3 className="text-base font-semibold">
          {g.name} <span className="text-sm font-normal text-muted-foreground tabular-nums">{g.totals.files}</span>
        </h3>
        <Link href={readHref} className="text-sm font-medium text-primary hover:underline">
          Read this cluster →
        </Link>
        <p className="text-sm text-muted-foreground tabular-nums">
          {g.totals.median_duration_s != null && `typical ${Math.round(g.totals.median_duration_s)}s · `}
          {fmt.money(g.totals.spend)} spent · hook {pct(g.totals.hook_rate)} · link CTR {pct(g.totals.link_ctr)} ·{' '}
          {g.totals.cpa != null ? `${fmt.money(g.totals.cpa)} per purchase` : 'no purchases'} · {g.totals.purchases} purchases
        </p>
      </div>
      <ul className="grid grid-cols-4 gap-2 sm:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10">
        {g.files.map((f) => {
          const poster = blobUrl(f.poster_url);
          const body = (
            <span className="block space-y-1">
              <span className="relative block aspect-[4/5] overflow-hidden rounded-md bg-muted">
                {poster ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={poster} alt="" loading="lazy" className="h-full w-full object-cover" />
                ) : (
                  <ImageOff className="absolute inset-0 m-auto size-4 text-muted-foreground" aria-hidden="true" />
                )}
                {f.duration_s != null && (
                  <span className="absolute right-1 bottom-1 rounded bg-black/60 px-1 text-[10px] text-white tabular-nums">{Math.round(f.duration_s)}s</span>
                )}
              </span>
              <span translate="no" className="line-clamp-2 text-[10px] leading-tight text-muted-foreground [overflow-wrap:anywhere]">
                {f.name ?? 'Untitled'}
              </span>
            </span>
          );
          const tip = [f.name, f.reason].filter(Boolean).join('\n\n');
          return (
            <li key={f.id} title={tip}>
              {f.creative_id ? (
                <Link href={`/creatives/${f.creative_id}`} className="block transition-opacity hover:opacity-80">
                  {body}
                </Link>
              ) : (
                body
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
