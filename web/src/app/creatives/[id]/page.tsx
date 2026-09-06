import { CreativeCard } from '@/components/creative-card';
import { MetricChart } from '@/components/metric-chart';
import { TagList } from '@/components/tag-list';
import { api } from '@/lib/api';

const iso = (d: Date) => d.toISOString().slice(0, 10);

export default async function CreativeDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const until = new Date(); const since = new Date(until.getTime() - 89 * 86400000);
  const [creative, tags, metrics, dims, similar] = await Promise.all([
    api.creative(id), api.creativeTags(id), api.creativeMetrics(id, iso(since), iso(until)), api.dimensions(), api.similar(id)]);
  return (
    <div className="grid gap-8 lg:grid-cols-3">
      <div className="space-y-3">
        {creative.primary_asset_url && (creative.format === 'video'
          ? <video src={creative.primary_asset_url} controls className="w-full rounded" />
          : <img src={creative.primary_asset_url} alt="" className="w-full rounded" />)}
        <h1 className="text-lg font-semibold">{creative.headline}</h1>
        <p className="text-sm text-muted-foreground">{creative.primary_text}</p>
        <p className="text-xs">CTA: {creative.cta_type} · {creative.link_url}</p>
      </div>
      <div className="space-y-6 lg:col-span-2">
        <section><h2 className="mb-2 font-semibold">Performance (90 days)</h2><MetricChart rows={metrics} /></section>
        <section>
          <h2 className="mb-2 font-semibold">Tags</h2>
          {creative.format === 'carousel' && <p className="mb-2 text-xs text-muted-foreground">Carousel · tags reflect all cards</p>}
          {tags.length ? <TagList tags={tags} dimensions={dims} /> : <p className="text-sm text-muted-foreground">Not tagged yet.</p>}
        </section>
        <section><h2 className="mb-2 font-semibold">Similar creatives</h2><div className="grid grid-cols-3 gap-3 md:grid-cols-5">{similar.map((s) => <CreativeCard key={s.id} creative={s} />)}</div></section>
      </div>
    </div>
  );
}
