import Link from 'next/link';
import { ChevronRight, ExternalLink } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { CLICK_FLOOR, gap, gapText, gapTone, IMPRESSION_FLOOR } from '@/lib/compare';
import type { Formatters } from '@/lib/format';
import type { CreativeSummary, Diagnosis, Footage, FootageAd, Scope, Totals } from '@/lib/types';

// Meta mints a new creative id whenever an ad is copied into another campaign or its file is
// uploaded again, so one file usually runs as several Meta creatives. ScopePanel is the one
// place the page deals with that: what the numbers cover, the switch between the two
// readings, the totals, and the ads those totals are made of. They share an enclosure because
// they are one thing. The switch decides which ads count, the totals are their sum, and the
// table is the breakdown of the totals; apart, a reader has to carry the link in their head.
const perPurchase = (t: Pick<Totals, 'spend' | 'purchases'>, fmt: Formatters) => (t.purchases > 0 ? fmt.money(t.spend / t.purchases) : '–');

// `adsOpen` keeps the breakdown open across the links inside it, so stepping from one copy
// to the next does not close the list being stepped through.
const hrefFor = (creativeId: string, scope: Scope, adsOpen: boolean) => {
  const q = new URLSearchParams();
  if (scope === 'creative') q.set('scope', 'creative');
  if (adsOpen) q.set('ads', 'open');
  const s = q.toString();
  return `/creatives/${creativeId}${s ? `?${s}` : ''}`;
};

function Covers({ footage, mine, pooled }: { footage: Footage; mine: FootageAd[]; pooled: boolean }) {
  const { ads, creative_ids: creativeIds } = footage;
  const campaigns = new Set(ads.map((a) => a.campaign_id).filter(Boolean)).size;
  const across = campaigns > 1 ? ` across ${campaigns} campaigns` : '';
  if (ads.length === 0) return <>No ad is running this creative.</>;
  if (ads.length === 1) {
    const a = ads[0];
    return (
      <>
        <strong className="font-semibold text-foreground">One ad runs this file.</strong>{' '}
        <span translate="no" className="break-all">
          {a.name ?? a.platform_ad_id}
          {a.campaign_name ? ` · ${a.campaign_name}` : ''}
        </span>
      </>
    );
  }
  if (creativeIds.length < 2) {
    return (
      <>
        <strong className="font-semibold text-foreground">
          This file runs in {ads.length} ads{across}.
        </strong>{' '}
        They share one Meta creative, so every number on this page is their total.
      </>
    );
  }
  if (pooled) {
    return (
      <>
        <strong className="font-semibold text-foreground">
          This file runs in {ads.length} ads{across}.
        </strong>{' '}
        Meta gives every copy of an ad its own creative id. Every number on this page combines all of them.
      </>
    );
  }
  const first = mine[0];
  return (
    <>
      <strong className="font-semibold text-foreground">
        Showing {mine.length === 1 ? 'one ad' : `${mine.length} ads`} of the {ads.length} that run this file.
      </strong>{' '}
      {first && (
        <span translate="no" className="break-all">
          {first.name ?? first.platform_ad_id}
          {first.campaign_name ? ` · ${first.campaign_name}` : ''}
          {mine.length > 1 ? ` and ${mine.length - 1} more` : ''}.
        </span>
      )}{' '}
      Every number on this page covers this Meta creative only.
    </>
  );
}

// One headline number. A rate or a cost means little alone, so where the account has a
// figure for the same window it sits underneath with the gap to it. The gap is the plain
// arithmetic difference, signed as a buyer reads it (cost down is minus), and the colour
// says whether that direction is good. Gaps under 15% stay grey, the diagnosis's own bar.
// `neutral` keeps a gap grey whatever its size: cost per thousand impressions is set mostly
// by the audience and placements Meta chose, so colouring it would present an auction effect
// as a verdict on the creative.
type StatProps = { label: string; value: string; base?: string; baseLabel?: string; delta?: number | null; higherIsBetter?: boolean; neutral?: boolean; hint?: string };

function Stat({ label, value, base, baseLabel = 'account', delta, higherIsBetter, neutral, hint }: StatProps) {
  const tone = gapTone(delta, !!higherIsBetter, neutral);
  return (
    <div className="min-w-0 flex-1 basis-0 bg-card px-3 py-2.5" title={hint}>
      {/* Room for a two-line label on every card, so the numbers sit on one line across the row. */}
      <dt className="min-h-[2lh] text-xs leading-snug text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold tabular-nums">{value}</dd>
      {base && (
        <dd className="mt-0.5 text-xs text-muted-foreground tabular-nums">
          {delta != null && <span className={`font-semibold ${tone}`}>{gapText(delta)}</span>} vs {base}
          {/* On a phone three cards share a row; the number carries the comparison alone. */}
          <span className="hidden sm:inline"> {baseLabel}</span>
        </dd>
      )}
    </div>
  );
}

// The numbers are grouped by the diagnosis layer they belong to, in funnel order, so eleven
// figures read as four short groups and the eye lands on the group the verdict names.
function StatGroup({ title, children }: { title: string; children: React.ReactNode }) {
  const n = Array.isArray(children) ? children.filter(Boolean).length : 1;
  return (
    // A group claims room in proportion to its cards, so every card gets the same width.
    <div
      className="flex min-w-0 grow-[var(--n)] basis-0 flex-col bg-card"
      style={{ '--n': n } as React.CSSProperties}
    >
      <h3 className="border-b border-border px-3 pt-2 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
      <dl className="flex flex-1 gap-px bg-border">{children}</dl>
    </div>
  );
}

export function ScopePanel({
  footage,
  creativeId,
  scope,
  adsOpen,
  summary,
  diagnosis,
  isVideo,
  actId,
  fmt,
}: {
  footage: Footage;
  creativeId: string;
  scope: Scope;
  adsOpen: boolean;
  summary: CreativeSummary;
  diagnosis: Diagnosis;
  isVideo: boolean;
  actId?: string;
  fmt: Formatters;
}) {
  const { ads } = footage;
  const switchable = footage.creative_ids.length > 1;
  const pooled = scope === 'footage' || !switchable;
  const mine = pooled ? ads : ads.filter((a) => a.creative_id === creativeId);
  const { totals: t, baseline: b } = summary;
  const seen = t.impressions >= IMPRESSION_FLOOR && b.impressions >= IMPRESSION_FLOOR;
  const clicked = seen && t.link_clicks >= CLICK_FLOOR && b.link_clicks >= CLICK_FLOOR;
  const x = (n: number | null) => (n == null ? '–' : `${n.toFixed(2)}x`);
  const money = (n: number | null) => (n == null ? '–' : fmt.money(n));
  // A comparison shows only when its side of the funnel has enough delivery to read.
  const vs = (ok: boolean, mine: number | null, base: number | null, show: (n: number | null) => string) =>
    ok && base != null ? { base: show(base), delta: gap(mine, base) } : {};
  // Hold rate comes from the diagnosis, not the account pool: it is only fair against videos
  // of similar length, and the header must show the same comparison the retention layer does.
  const hold = diagnosis.tiers.find((tier) => tier.key === 'retention')?.metrics.find((m) => m.key === 'hold_rate');
  const note = seen
    ? `Last 30 days · account = ${summary.baseline_creatives} other ${isVideo ? 'videos' : 'creatives'}`
    : `Last 30 days · under ${IMPRESSION_FLOOR.toLocaleString('en-US')} impressions, too little to compare with the account`;
  // To a media buyer "creative" means the video, so the narrow reading is named by what it
  // actually narrows to: the ad, or ads, under this one Meta creative.
  const own = ads.filter((a) => a.creative_id === creativeId).length;
  const options: [Scope, string][] = [
    ['footage', `All ${ads.length} ads combined`],
    ['creative', own === 1 ? 'This ad only' : `These ${own} ads only`],
  ];
  return (
    <section aria-label="What this page covers" id="ads" className="scroll-mt-16 overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-2.5">
        <p className="max-w-prose min-w-0 text-sm text-muted-foreground">
          <Covers footage={footage} mine={mine} pooled={pooled} />
        </p>
        {switchable && (
          <nav aria-label="Which ads the numbers cover" className="inline-flex shrink-0 rounded-lg border border-border bg-muted p-0.5 text-sm">
            {options.map(([value, label]) => (
              <Link
                key={value}
                href={hrefFor(creativeId, value, adsOpen)}
                scroll={false}
                aria-current={scope === value ? 'page' : undefined}
                className={`rounded-md px-3 py-1.5 font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
                  scope === value ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {label}
              </Link>
            ))}
          </nav>
        )}
      </div>
      {/* One row, always: the funnel reads left to right, every card an equal share. */}
      <div className="flex gap-px border-y border-border bg-border">
        <StatGroup title="Delivery">
          <Stat label="Spend" value={fmt.money(t.spend)} />
          <Stat label="Impressions" value={fmt.count(t.impressions)} />
          <Stat label="CPM" hint="Cost per 1,000 impressions. Set mostly by the audience and placements Meta chose, so the gap is not coloured." value={money(t.cpm)} {...vs(seen, t.cpm, b.cpm, money)} neutral />
        </StatGroup>
        <StatGroup title="Attention">
          {isVideo && <Stat label="Hook rate" hint="3-second views ÷ impressions" value={fmt.pct(t.hook_rate, 1)} {...vs(seen, t.hook_rate, b.hook_rate, (n) => fmt.pct(n, 1))} higherIsBetter />}
          <Stat label="Link CTR" hint="Link clicks ÷ impressions" value={fmt.pct(t.link_ctr)} {...vs(seen, t.link_ctr, b.link_ctr, (n) => fmt.pct(n))} higherIsBetter />
        </StatGroup>
        {isVideo && hold && hold.value != null && (
          <StatGroup title="Retention">
            <Stat
              label="Hold rate"
              hint="ThruPlays ÷ 3-second views, against videos of similar length"
              value={fmt.pct(hold.value, 0)}
              base={hold.baseline == null ? undefined : fmt.pct(hold.baseline, 0)}
              baseLabel="similar length"
              delta={hold.baseline == null ? null : gap(hold.value, hold.baseline)}
              higherIsBetter
            />
          </StatGroup>
        )}
        <StatGroup title="Conversion">
          <Stat label="Click to purchase" hint="Purchases ÷ link clicks" value={fmt.pct(t.click_to_purchase)} {...vs(clicked, t.click_to_purchase, b.click_to_purchase, (n) => fmt.pct(n))} higherIsBetter />
          <Stat label="Purchases" value={fmt.count(t.purchases)} />
          <Stat label="Cost per purchase" value={money(t.cpa)} {...vs(clicked, t.cpa, b.cpa, money)} />
          <Stat label="AOV" hint="Average order value: purchase value ÷ purchases" value={t.purchases ? money(t.aov) : '–'} {...vs(clicked && t.purchases > 0, t.aov, b.aov, money)} higherIsBetter />
          <Stat label="ROAS" hint="Purchase value ÷ spend, as attributed by Meta" value={t.purchases ? x(t.roas) : '–'} {...vs(clicked && t.purchases > 0, t.roas, b.roas, x)} higherIsBetter />
        </StatGroup>
      </div>
      {ads.length > 1 ? (
        <details open={adsOpen} className="group/ads">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-sm hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
            <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-open/ads:rotate-90 motion-reduce:transition-none" aria-hidden="true" />
            <span className="font-medium">
              <span className="group-open/ads:hidden">Show</span>
              <span className="hidden group-open/ads:inline">Hide</span> the {ads.length} ads behind these numbers
            </span>
            <span className="ml-auto text-xs text-muted-foreground">{note}</span>
          </summary>
          <AdsTable footage={footage} creativeId={creativeId} pooled={pooled} actId={actId} fmt={fmt} />
        </details>
      ) : (
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{note}</p>
      )}
    </section>
  );
}

// One row per ad. A row opens that ad's own creative with the list still open, which makes
// the table the way to step through the copies of a file.
function AdsTable({ footage, creativeId, pooled, actId, fmt }: { footage: Footage; creativeId: string; pooled: boolean; actId?: string; fmt: Formatters }) {
  return (
    <div className="overflow-x-auto border-t border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Ad</TableHead>
            <TableHead>Campaign</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Spend</TableHead>
            <TableHead className="text-right">Impr.</TableHead>
            <TableHead className="text-right">CTR</TableHead>
            <TableHead className="text-right">Purchases</TableHead>
            <TableHead className="text-right">Cost / purchase</TableHead>
            {actId && <TableHead className="w-10"><span className="sr-only">Open in Ads Manager</span></TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {footage.ads.map((a) => {
            const counted = pooled || a.creative_id === creativeId;
            return (
              <TableRow key={a.id} className={pooled ? undefined : counted ? 'bg-muted/60 font-medium' : 'text-muted-foreground'} aria-current={!pooled && counted ? 'true' : undefined}>
                <TableCell className="max-w-64 min-w-44">
                  <Link
                    href={hrefFor(a.creative_id, 'creative', true)}
                    scroll={false}
                    translate="no"
                    className="line-clamp-3 break-all whitespace-normal underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    {a.name ?? a.platform_ad_id}
                  </Link>
                </TableCell>
                <TableCell className="max-w-56 min-w-40 whitespace-normal text-muted-foreground" translate="no">
                  <span className="line-clamp-3 break-all">{a.campaign_name ?? a.campaign_id ?? '–'}</span>
                </TableCell>
                <TableCell>
                  <Badge variant={a.status === 'ACTIVE' ? 'success' : 'outline'} className="capitalize">
                    {(a.status ?? 'unknown').toLowerCase()}
                  </Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums">{fmt.money(a.spend)}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.count(a.impressions)}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.pct(a.ctr)}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.count(a.purchases)}</TableCell>
                <TableCell className="text-right tabular-nums">{perPurchase(a, fmt)}</TableCell>
                {actId && (
                  <TableCell>
                    <a
                      href={`https://adsmanager.facebook.com/adsmanager/manage/ads?act=${actId}&selected_ad_ids=${a.platform_ad_id}`}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={`Open ${a.name ?? a.platform_ad_id} in Ads Manager`}
                      title="Open in Ads Manager"
                      className="inline-flex rounded-sm p-1 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                    >
                      <ExternalLink className="size-4" aria-hidden="true" />
                    </a>
                  </TableCell>
                )}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

// Same file, different headline or primary text: the cleanest copy comparison the account
// offers. This is analysis, not scope, so it sits with the analysis below the chart.
export function CopyVariants({ footage, fmt }: { footage: Footage; fmt: Formatters }) {
  const variants = footage.copy_variants;
  if (variants.length < 2) return null;
  return (
    <section id="copy" className="scroll-mt-16">
      <h2 className="mb-3 text-sm font-semibold">Copy this file ran with</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {variants.map((v) => (
          <div key={v.creative_ids[0]} className="space-y-2 rounded-lg border border-border p-3 text-sm">
            <div className="font-medium text-balance break-words">{v.headline ?? 'No headline'}</div>
            <p className="line-clamp-3 break-words text-muted-foreground">{v.primary_text ?? 'No primary text'}</p>
            <div className="text-xs text-muted-foreground tabular-nums">
              {fmt.money(v.spend)} · {fmt.pct(v.ctr)} CTR · {fmt.count(v.purchases)} purchases · {perPurchase(v, fmt)} per purchase
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
