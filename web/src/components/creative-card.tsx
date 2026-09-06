import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import type { Creative } from '@/lib/types';

export function CreativeCard({ creative }: { creative: Creative }) {
  return (
    <Link href={`/creatives/${creative.id}`} className="block overflow-hidden rounded border hover:shadow">
      <div className="aspect-square bg-muted">
        {creative.primary_asset_url && (creative.format === 'video'
          ? <video src={creative.primary_asset_url} muted className="h-full w-full object-cover" />
          : <img src={creative.primary_asset_url} alt="" className="h-full w-full object-cover" />)}
      </div>
      <div className="space-y-1 p-2 text-sm"><Badge variant="outline">{creative.format}</Badge><div className="line-clamp-2">{creative.headline ?? creative.primary_text ?? creative.platform_creative_id}</div></div>
    </Link>
  );
}
