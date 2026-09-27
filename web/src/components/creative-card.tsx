import Link from 'next/link';
import { Clapperboard, Copy, ImageOff, Layers, PlayCircle } from 'lucide-react';
import { CardMedia } from '@/components/creative-card-media';
import { blobUrl } from '@/lib/blob-url';
import { CLICK_FLOOR, gap, gapText, gapTone, IMPRESSION_FLOOR } from '@/lib/compare';
import type { Formatters } from '@/lib/format';
import type { Creative, GridRates } from '@/lib/types';

const FORMAT_LABEL = { video: 'Video', image: 'Image', carousel: 'Carousel', unresolved: 'Media unavailable' } as const;
const formatIcon = { video: PlayCircle, carousel: Layers, image: null, unresolved: null } as const;
export const PEERS = { video: 'videos', image: 'images', carousel: 'carousels', unresolved: 'ads without media' } as const;

// The fourth number follows the sort, so a grid sorted by purchases shows purchases. Otherwise
// it is the attention number for the format: hook rate for video, link CTR for the rest.
export type CardExtra = 'hook_rate' | 'link_ctr' | 'purchases' | 'impressions';
export const extraFor = (sort: string | undefined, format: Creative['format']): CardExtra =>
  sort === 'purchases' || sort === 'impressions' || sort === 'link_ctr' ? sort : sort === 'hook_rate' || format === 'video' ? 'hook_rate' : 'link_ctr';

type Cell = { label: string; short?: string; value: string; delta?: number | null; higherIsBetter?: boolean; hint?: string };

function cells(t: GridRates, b: GridRates, extra: CardExtra, fmt: Formatters, peers: string): Cell[] {
  // The same floors as the creative page: a gap shows only where both sides can carry it.
  const seen = t.impressions >= IMPRESSION_FLOOR && b.impressions >= IMPRESSION_FLOOR;
  const clicked = seen && t.link_clicks >= CLICK_FLOOR && b.link_clicks >= CLICK_FLOOR;
  const x = (n: number | null) => (n == null ? '–' : `${n.toFixed(2)}x`);
  const money = (n: number | null) => (n == null ? '–' : fmt.money(n));
  const pct = (n: number | null) => fmt.pct(n, 1);
  const vs = (ok: boolean, mine: number | null, base: number | null, show: (n: number | null) => string, higherIsBetter: boolean) =>
    ok && base != null && mine != null ? { delta: gap(mine, base), higherIsBetter, hint: `${show(base)} across the other ${peers}` } : {};
  const last: Record<CardExtra, Cell> = {
    hook_rate: { label: 'Hook rate', value: pct(t.hook_rate), ...vs(seen, t.hook_rate, b.hook_rate, pct, true) },
    link_ctr: { label: 'Link CTR', value: pct(t.link_ctr), ...vs(seen, t.link_ctr, b.link_ctr, pct, true) },
    purchases: { label: 'Purchases', value: fmt.count(t.purchases) },
    impressions: { label: 'Impressions', value: fmt.count(t.impressions) },
  };
  return [
    { label: 'Spend', value: fmt.money(t.spend) },
    { label: 'ROAS', value: x(t.roas), ...vs(clicked, t.roas, b.roas, x, true) },
    { label: 'Cost / purchase', short: 'CPA', value: money(t.cpa), ...vs(clicked, t.cpa, b.cpa, money, false) },
    last[extra],
  ];
}

// `ungrouped` is the one-card-per-Meta-creative view: its cards open the creative's own
// numbers, not the pooled footage page.
export function CreativeCard({ creative, fmt, extra, ungrouped = false, eager = false }: { creative: Creative; fmt: Formatters; extra: CardExtra; ungrouped?: boolean; eager?: boolean }) {
  const media = blobUrl(creative.primary_asset_url);
  const isVideo = creative.format === 'video';
  const poster = blobUrl(creative.poster_url ?? null) ?? (isVideo ? null : media);
  const Icon = formatIcon[creative.format];
  // A buyer knows an ad by the name they gave it in Ads Manager; copy is the fallback.
  const title = creative.ad_name ?? creative.headline ?? creative.primary_text ?? creative.platform_creative_id;
  const ads = creative.ad_count ?? 0;
  const grouped = (creative.creative_count ?? 1) > 1;
  const t = creative.totals;
  const delivered = !!t && t.impressions > 0;
  return (
    <div className="group/card relative flex">
    <Link
      href={`/creatives/${creative.id}${ungrouped ? '?scope=creative' : ''}`}
      className="group flex w-full min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card transition-shadow hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
    >
      <div className="relative aspect-[4/5] overflow-hidden bg-muted">
        {poster || media ? (
          <CardMedia poster={poster} video={isVideo ? media : null} alt={creative.headline ?? title} eager={eager} />
        ) : (
          <div
            className="flex h-full w-full flex-col items-center justify-center gap-1.5 px-4 text-center text-xs text-muted-foreground"
            title="We could not retrieve this ad's image or video from Meta, so its format is unconfirmed. Its numbers are complete."
          >
            <ImageOff className="size-5" aria-hidden="true" />
            Media unavailable
          </div>
        )}
        {grouped && (
          <span
            className="absolute top-2 left-2 inline-flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums"
            title={`The same file runs in ${ads} ads. Numbers are their total.`}
          >
            <Copy className="size-3" aria-hidden="true" />
            {ads} ads
          </span>
        )}
        {Icon && (
          <span className="absolute top-2 right-2 rounded-full bg-black/60 p-1 text-white">
            <Icon className="size-3.5" aria-hidden="true" />
            <span className="sr-only">{FORMAT_LABEL[creative.format]}</span>
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-2.5">
        <div>
          <div translate="no" title={title} className="line-clamp-2 text-[13px] leading-snug font-medium [overflow-wrap:anywhere] text-foreground">
            {title}
          </div>
          {creative.ad_name && creative.headline && <div className="mt-0.5 truncate text-xs text-muted-foreground">{creative.headline}</div>}
        </div>
        {delivered ? (
          <CardMetrics totals={t} baseline={creative.baseline ?? t} extra={extra} fmt={fmt} peers={`${PEERS[creative.format]} in this account`} />
        ) : (
          <p className="mt-auto border-t border-border pt-2 text-xs text-muted-foreground">No delivery in the last 30 days</p>
        )}
      </div>
    </Link>
    {/* A sibling of the card's link, not inside it: links cannot nest. Shown on hover or focus. */}
    {isVideo && (
      <Link
        href={`/make/from/${creative.id}`}
        className="absolute top-2 right-10 inline-flex items-center gap-1 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px] font-medium text-white opacity-0 transition-opacity group-hover/card:opacity-100 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <Clapperboard className="size-3" aria-hidden="true" /> Make versions
      </Link>
    )}
    </div>
  );
}

// The card's four numbers, each with its gap to `peers` (a phrase: "videos in this account").
export function CardMetrics({ totals, baseline, extra, fmt, peers }: { totals: GridRates; baseline: GridRates; extra: CardExtra; fmt: Formatters; peers: string }) {
  return (
    <dl className="mt-auto grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-border pt-2">
      {cells(totals, baseline, extra, fmt, peers).map((c) => (
        <div key={c.label} className="min-w-0" title={c.hint}>
          <dt className="truncate text-[11px] text-muted-foreground">
            {/* A two-up phone card is too narrow for the long label. */}
            {c.short ? (
              <>
                <span className="sm:hidden" aria-hidden="true">
                  {c.short}
                </span>
                <span className="sr-only sm:not-sr-only">{c.label}</span>
              </>
            ) : (
              c.label
            )}
          </dt>
          <dd className="flex flex-wrap items-baseline gap-x-1 text-sm font-semibold tabular-nums">
            {c.value}
            {c.delta != null && <span className={`text-[11px] font-semibold ${gapTone(c.delta, !!c.higherIsBetter)}`}>{gapText(c.delta)}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}
