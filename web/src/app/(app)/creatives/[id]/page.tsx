import { MetricChart } from '@/components/metric-chart';
import { TagList } from '@/components/tag-list';
import { Badge } from '@/components/ui/badge';
import { api } from '@/lib/api';
import { blobUrl } from '@/lib/blob-url';
import { requireAuth } from '@/lib/require-auth.server';

const iso = (d: Date) => d.toISOString().slice(0, 10);

export default async function CreativeDetail({ params }: { params: Promise<{ id: string }> }) {
  await requireAuth();
  const { id } = await params;
  const until = new Date();
  const since = new Date(until.getTime() - 89 * 86400000);
  const [creative, tags, metrics, dims] = await Promise.all([
    api.creative(id),
    api.creativeTags(id),
    api.creativeMetrics(id, iso(since), iso(until)),
    api.dimensions(),
  ]);
  const assetSrc = blobUrl(creative.primary_asset_url);
  return (
    <div className="grid gap-8 lg:grid-cols-3">
      <div className="space-y-3">
        <div className="overflow-hidden rounded-xl border border-border bg-muted">
          {assetSrc &&
            (creative.format === 'video' ? (
              <video src={assetSrc} controls className="w-full" />
            ) : (
              <img src={assetSrc} alt={creative.headline ?? ''} className="w-full" />
            ))}
        </div>
        <Badge variant="outline" className="capitalize">
          {creative.format}
        </Badge>
        <h1 className="text-lg font-semibold text-balance">{creative.headline}</h1>
        <p className="text-sm text-muted-foreground">{creative.primary_text}</p>
        {(creative.cta_type || creative.link_url) && (
          <p className="text-xs text-muted-foreground">
            {creative.cta_type}
            {creative.cta_type && creative.link_url ? ' · ' : ''}
            {creative.link_url}
          </p>
        )}
      </div>
      <div className="space-y-8 lg:col-span-2">
        <section>
          <h2 className="mb-3 text-sm font-semibold">Performance (90 days)</h2>
          <MetricChart rows={metrics} />
        </section>
        <section>
          <h2 className="mb-3 text-sm font-semibold">Tags</h2>
          {creative.format === 'carousel' && <p className="mb-2 text-xs text-muted-foreground">Carousel · tags reflect all cards</p>}
          {tags.length ? <TagList tags={tags} dimensions={dims} /> : <p className="text-sm text-muted-foreground">Not tagged yet.</p>}
        </section>
      </div>
    </div>
  );
}
