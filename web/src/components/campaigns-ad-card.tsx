import Link from 'next/link';
import { ImageOff, Layers, PlayCircle } from 'lucide-react';
import { cn } from 'cn';
import { CardMedia } from '@/components/creative-card-media';
import { CardMetrics, extraFor, PEERS } from '@/components/creative-card';
import { blobUrl } from '@/lib/blob-url';
import type { Formatters } from '@/lib/format';
import type { LibraryAd } from '@/lib/types';

const formatIcon = {
  video: PlayCircle,
  carousel: Layers,
  image: null,
  unresolved: null,
} as const;

// Meta's effective status in a buyer's words. "Ad set off" and "Campaign off" matter: the ad's
// own switch is on, so Ads Manager's ad row alone would not say why it is not delivering.
const DELIVERY: Record<string, { label: string; tone: 'live' | 'warn' | 'off' }> = {
  ACTIVE: { label: 'Live', tone: 'live' },
  ADSET_PAUSED: { label: 'Ad set off', tone: 'off' },
  CAMPAIGN_PAUSED: { label: 'Campaign off', tone: 'off' },
  PAUSED: { label: 'Off', tone: 'off' },
  ARCHIVED: { label: 'Archived', tone: 'off' },
  DELETED: { label: 'Deleted', tone: 'off' },
  WITH_ISSUES: { label: 'Has issues', tone: 'warn' },
  DISAPPROVED: { label: 'Rejected', tone: 'warn' },
  PENDING_REVIEW: { label: 'In review', tone: 'warn' },
  IN_PROCESS: { label: 'Processing', tone: 'warn' },
};

export function deliveryOf(status: string | null) {
  if (!status) return null;
  return (
    DELIVERY[status] ?? {
      label: status.toLowerCase().replace(/_/g, ' '),
      tone: 'off' as const,
    }
  );
}

export type LibraryScope = 'account' | 'campaign' | 'adset';
const SCOPE_WORD: Record<LibraryScope, string> = {
  account: 'account',
  campaign: 'campaign',
  adset: 'ad set',
};

export function LibraryAdCard({ ad, fmt, scope, sort, eager = false }: { ad: LibraryAd; fmt: Formatters; scope: LibraryScope; sort: string; eager?: boolean }) {
  const media = blobUrl(ad.primary_asset_url);
  const isVideo = ad.format === 'video';
  const poster = blobUrl(ad.poster_url) ?? (isVideo ? null : media);
  const Icon = formatIcon[ad.format];
  const title = ad.name ?? ad.headline ?? ad.platform_ad_id;
  const delivery = deliveryOf(ad.effective_status);
  const t = ad.totals;
  // Where the ad sits, one level below what is being browsed: the reader already knows the rest.
  const where = scope === 'account' ? [ad.campaign_name, ad.adset_name].filter(Boolean).join(' › ') : scope === 'campaign' ? ad.adset_name : null;
  return (
    <Link
      href={`/creatives/${ad.creative_id}?scope=creative`}
      className="group flex flex-col overflow-hidden rounded-xl border border-border bg-card transition-shadow hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
    >
      <div className="relative aspect-[4/5] overflow-hidden bg-muted">
        {poster || media ? (
          <CardMedia poster={poster} video={isVideo ? media : null} alt={ad.headline ?? title} eager={eager} />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 px-4 text-center text-xs text-muted-foreground">
            <ImageOff className="size-5" aria-hidden="true" />
            Media unavailable
          </div>
        )}
        {delivery && (
          <span className="absolute top-2 left-2 inline-flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white">
            <span
              className={cn('size-1.5 rounded-full', delivery.tone === 'live' ? 'bg-emerald-400' : delivery.tone === 'warn' ? 'bg-amber-400' : 'bg-white/50')}
              aria-hidden="true"
            />
            {delivery.label}
          </span>
        )}
        {Icon && (
          <span className="absolute top-2 right-2 rounded-full bg-black/60 p-1 text-white">
            <Icon className="size-3.5" aria-hidden="true" />
            <span className="sr-only">{ad.format}</span>
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-2.5">
        <div>
          <div translate="no" title={title} className="line-clamp-2 text-[13px] leading-snug font-medium [overflow-wrap:anywhere] text-foreground">
            {title}
          </div>
          {where ? (
            <div translate="no" title={where} className="mt-0.5 truncate text-xs text-muted-foreground">
              {where}
            </div>
          ) : (
            ad.headline && <div className="mt-0.5 truncate text-xs text-muted-foreground">{ad.headline}</div>
          )}
        </div>
        {t.impressions > 0 ? (
          <CardMetrics
            totals={t}
            baseline={ad.baseline}
            extra={extraFor(sort, ad.format)}
            fmt={fmt}
            peers={`${PEERS[ad.format]} in this ${SCOPE_WORD[scope]}`}
          />
        ) : (
          <p className="mt-auto border-t border-border pt-2 text-xs text-muted-foreground">No delivery in these 30 days</p>
        )}
      </div>
    </Link>
  );
}
