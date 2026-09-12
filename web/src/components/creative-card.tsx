import Link from 'next/link';
import { HelpCircle, Layers, PlayCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { blobUrl } from '@/lib/blob-url';
import type { Creative } from '@/lib/types';

const formatIcon = { video: PlayCircle, carousel: Layers, image: null, unresolved: HelpCircle } as const;

export function CreativeCard({ creative }: { creative: Creative }) {
  const src = blobUrl(creative.primary_asset_url);
  const Icon = formatIcon[creative.format];
  const caption = creative.headline ?? creative.primary_text ?? creative.platform_creative_id;
  const metrics =
    creative.spend_30d > 0
      ? `$${Math.round(creative.spend_30d).toLocaleString('en-US')} · ${((creative.ctr_30d ?? 0) * 100).toFixed(1)}% CTR`
      : 'No spend in last 30 days';
  return (
    <Link
      href={`/creatives/${creative.id}`}
      className="group block overflow-hidden rounded-xl border border-border bg-card transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      <div className="relative aspect-square overflow-hidden bg-muted">
        {src ? (
          creative.format === 'video' ? (
            <video src={src} muted playsInline className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
          ) : (
            <img
              src={src}
              alt={caption ?? ''}
              className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
            />
          )
        ) : (
          <div className="flex h-full w-full items-center justify-center text-xs text-muted-foreground">No preview</div>
        )}
        {Icon && (
          <span className="absolute top-2 right-2 rounded-full bg-black/55 p-1 text-white">
            <Icon className="size-3.5" aria-hidden="true" />
          </span>
        )}
      </div>
      <div className="space-y-1.5 p-2.5 text-sm">
        <Badge variant="outline" className="capitalize">
          {creative.format}
        </Badge>
        <div className="line-clamp-2 leading-snug text-foreground">{caption}</div>
        <div className="text-xs text-muted-foreground">{metrics}</div>
      </div>
    </Link>
  );
}
