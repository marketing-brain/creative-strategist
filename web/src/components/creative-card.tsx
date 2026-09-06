import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { blobUrl } from '@/lib/blob-url';
import type { Creative } from '@/lib/types';

export function CreativeCard({ creative }: { creative: Creative }) {
  const src = blobUrl(creative.primary_asset_url);
  return (
    <Link href={`/creatives/${creative.id}`} className="block overflow-hidden rounded border hover:shadow">
      <div className="aspect-square bg-muted">
        {src && (creative.format === 'video'
          ? <video src={src} muted className="h-full w-full object-cover" />
          : <img src={src} alt="" className="h-full w-full object-cover" />)}
      </div>
      <div className="space-y-1 p-2 text-sm"><Badge variant="outline">{creative.format}</Badge><div className="line-clamp-2">{creative.headline ?? creative.primary_text ?? creative.platform_creative_id}</div></div>
    </Link>
  );
}
