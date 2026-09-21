import { cache } from 'react';
import type { Metadata } from 'next';
import { ClampedCopy, CreativeMedia } from '@/components/creative-detail-client';
import { AdProfile, NextTests, PATTERN_FOOTNOTE, tierEvidence } from '@/components/ad-profile';
import { CreativeHeader, SectionNav, creativeTitle } from '@/components/creative-header';
import { DiagnosisPanel } from '@/components/diagnosis-panel';
import { CopyVariants, ScopePanel } from '@/components/footage-panel';
import { MetricChart } from '@/components/metric-chart';
import { PlayCurveChart } from '@/components/play-curve-chart';
import { TagList } from '@/components/tag-list';
import { api } from '@/lib/api';
import { blobUrl } from '@/lib/blob-url';
import { formatters } from '@/lib/format';
import { requireAuth } from '@/lib/require-auth.server';
import type { Creative, Scope } from '@/lib/types';

// Meta's enum, SHOP_NOW, as the button the viewer saw: Shop now.
const cta = (slug: string) => slug.charAt(0) + slug.slice(1).toLowerCase().replaceAll('_', ' ');
const iso = (d: Date) => d.toISOString().slice(0, 10);
// The tab title and the page both need the creative; one request serves both.
const getCreative = cache((id: string) => api.creative(id));

type Props = { params: Promise<{ id: string }>; searchParams: Promise<{ scope?: string; ads?: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    return { title: creativeTitle(await getCreative(id)) };
  } catch {
    return { title: 'Creative' };
  }
}

// The ad's own words. Rendered once in the media column on wide screens and once lower down
// on narrow ones, where a tall video plus the full copy would otherwise push the diagnosis
// three screens down.
function AdCopy({ creative, className }: { creative: Creative; className: string }) {
  if (!creative.primary_text && !creative.cta_type && !creative.link_url) return null;
  return (
    <section aria-label="Ad copy" className={`space-y-2 ${className}`}>
      {creative.primary_text && <ClampedCopy text={creative.primary_text} />}
      {(creative.cta_type || creative.link_url) && (
        <p className="text-xs break-all text-muted-foreground" translate="no">
          {creative.cta_type && cta(creative.cta_type)}
          {creative.cta_type && creative.link_url ? ' · ' : ''}
          {creative.link_url}
        </p>
      )}
    </section>
  );
}

export default async function CreativeDetail({ params, searchParams }: Props) {
  await requireAuth();
  const { id } = await params;
  // Default: every ad running this file, combined. ?scope=creative narrows the numbers to
  // this one Meta creative, which is what reconciles against a single row in Ads Manager.
  const sp = await searchParams;
  const scope: Scope = sp.scope === 'creative' ? 'creative' : 'footage';
  const until = new Date();
  const since = new Date(until.getTime() - 89 * 86400000);
  const [creative, tags, metrics, dims, diagnosis, footage, accounts, patterns, summary, playCurve] = await Promise.all([
    getCreative(id),
    api.creativeTags(id),
    api.creativeMetrics(id, iso(since), iso(until), scope),
    api.dimensions(),
    api.creativeDiagnosis(id, scope),
    api.creativeFootage(id),
    api.accounts(),
    api.creativePatterns(id),
    api.creativeSummary(id, scope),
    // Optional evidence: a failure leaves the retention layer on its quartile chart.
    api.creativePlayCurve(id, scope).catch(() => null),
  ]);
  const account = accounts.find((a) => a.id === creative.ad_account_id);
  const fmt = formatters(account?.currency);
  const isVideo = creative.format === 'video';
  const aspect = tags.find((t) => t.dimension === 'aspect_ratio')?.value;
  const retention = diagnosis.tiers.find((t) => t.key === 'retention');
  const beats = tags.filter((t) => t.dimension === 'beats' && t.t_start_s != null).map((t) => ({ name: t.value.replaceAll('_', ' '), t: t.t_start_s as number }));
  return (
    <div className="space-y-6">
      <CreativeHeader creative={creative} footage={footage} />
      <ScopePanel
        footage={footage}
        creativeId={id}
        scope={scope}
        adsOpen={sp.ads === 'open'}
        summary={summary}
        diagnosis={diagnosis}
        isVideo={isVideo}
        actId={account?.platform_account_id.replace(/^act_/, '')}
        fmt={fmt}
      />
      <div className="grid gap-8 lg:grid-cols-3">
        <aside aria-label="The ad" className="space-y-3 lg:sticky lg:top-6 lg:max-h-[calc(100dvh-3rem)] lg:self-start lg:overflow-y-auto">
          <CreativeMedia src={blobUrl(creative.primary_asset_url)} format={creative.format} alt={creativeTitle(creative)} aspect={aspect} />
          <AdCopy creative={creative} className="hidden lg:block" />
        </aside>
        <div className="min-w-0 space-y-8 lg:col-span-2">
          <SectionNav hasCopyVariants={footage.copy_variants.length > 1} />
          {/* A media buyer reads this page to decide what to do with the ad. So the verdict
              and the tests it suggests come first, beside the video; then whether it is
              trending; then what the ad is, for the reader who did not make it. */}
          <div id="diagnosis" className="scroll-mt-16">
            <DiagnosisPanel
              diagnosis={diagnosis}
              evidence={tierEvidence(diagnosis, tags, patterns, isVideo, fmt)}
              afterVerdict={<NextTests title={creativeTitle(creative)} diagnosis={diagnosis} tags={tags} patterns={patterns} fmt={fmt} />}
              footnote={patterns.length > 0 ? PATTERN_FOOTNOTE : undefined}
              retentionChart={playCurve && retention ? <PlayCurveChart curve={playCurve} beats={beats} /> : undefined}
            />
          </div>
          <section id="performance" className="scroll-mt-16">
            <h2 className="mb-3 text-sm font-semibold">Trend (90 days)</h2>
            <MetricChart rows={metrics} currency={account?.currency} windowStart={diagnosis.window_start} windowEnd={diagnosis.window_end} />
            <p className="mt-1 text-xs text-muted-foreground">Bars are daily spend. The shaded band is the 30 days the diagnosis reads.</p>
          </section>
          <div id="overview" className="scroll-mt-16">
            <AdProfile tags={tags} format={creative.format} />
          </div>
          <AdCopy creative={creative} className="lg:hidden" />
          <CopyVariants footage={footage} fmt={fmt} />
          <section id="tags" className="scroll-mt-16">
            <h2 className="mb-3 text-sm font-semibold">Tags</h2>
            {creative.format === 'carousel' && <p className="mb-2 text-xs text-muted-foreground">Carousel · tags reflect all cards</p>}
            {tags.length ? (
              <TagList tags={tags} dimensions={dims} seekable={isVideo} />
            ) : (
              <p className="text-sm text-muted-foreground">Not tagged yet.</p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
