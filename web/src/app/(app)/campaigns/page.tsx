import Link from 'next/link';
import { redirect } from 'next/navigation';
import { ChevronDown, ChevronRight, SearchX } from 'lucide-react';
import { RememberGridUrl } from '@/components/creative-detail-client';
import { LibraryAdCard, type LibraryScope } from '@/components/campaigns-ad-card';
import { LibraryFilters } from '@/components/campaigns-filters';
import { LibraryTree } from '@/components/campaigns-tree';
import { Pagination } from '@/components/pagination';
import { api } from '@/lib/api';
import { formatters } from '@/lib/format';
import { adsetName, campaignName } from '@/lib/campaigns';
import { requireAuth } from '@/lib/require-auth.server';
import type { GridRates, LibraryCounts, LibraryDelivery } from '@/lib/types';

export const metadata = { title: 'Campaigns' };

const PAGE_SIZE = 60;

export default async function Library({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireAuth();
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);
  const delivery: LibraryDelivery = sp.delivery === 'live' || sp.delivery === 'all' ? sp.delivery : 'spent';
  const sort = sp.sort ?? 'spend';
  const campaignId = sp.campaign ?? null;
  const adsetId = campaignId ? (sp.adset ?? null) : null;
  const search = sp.q?.trim() || undefined;

  // Everything but the node and the page: what the tree's links carry forward.
  const keep = new URLSearchParams();
  for (const k of ['account_id', 'delivery', 'format', 'q', 'sort', 'order'] as const) if (sp[k]) keep.set(k, sp[k]!);
  const pageHref = (over: Record<string, string | null>) => {
    const q = new URLSearchParams(keep);
    if (campaignId) q.set('campaign', campaignId);
    if (adsetId) q.set('adset', adsetId);
    for (const [k, v] of Object.entries(over)) {
      if (v === null) q.delete(k);
      else q.set(k, v);
    }
    const s = q.toString();
    return `/campaigns${s ? `?${s}` : ''}`;
  };
  const makeHref = (p: number) => pageHref({ page: p > 1 ? String(p) : null });

  const apiQuery = new URLSearchParams({
    delivery,
    sort,
    order: sp.order ?? (sort === 'cpa' ? 'asc' : 'desc'),
    limit: String(PAGE_SIZE),
    offset: String((page - 1) * PAGE_SIZE),
  });
  if (sp.account_id) apiQuery.set('account_id', sp.account_id);
  if (campaignId) apiQuery.set('campaign_id', campaignId);
  if (adsetId) apiQuery.set('adset_id', adsetId);
  if (sp.format) apiQuery.set('format', sp.format);
  if (search) apiQuery.set('q', search);
  const treeQuery = sp.account_id ? `?account_id=${sp.account_id}` : '';

  const [tree, ads, accounts] = await Promise.all([api.libraryTree(treeQuery), api.libraryAds(`?${apiQuery}`), api.accounts()]);

  const totalPages = Math.max(1, Math.ceil(ads.total / PAGE_SIZE));
  if (page > totalPages) redirect(makeHref(totalPages));

  const campaign = campaignId ? tree.campaigns.find((c) => (c.id ?? '') === campaignId) : undefined;
  const adset = campaign && adsetId ? campaign.adsets.find((s) => (s.id ?? '') === adsetId) : undefined;
  const scope: LibraryScope = adset ? 'adset' : campaign ? 'campaign' : 'account';
  const currency =
    accounts.find((a) => a.id === (campaign?.ad_account_id ?? sp.account_id))?.currency ?? accounts.find((a) => a.status === 'active')?.currency ?? 'USD';
  const fmt = formatters(currency);

  const accountSpend = tree.campaigns.reduce((n, c) => n + c.totals.spend, 0);
  const accountCounts = tree.campaigns.reduce<LibraryCounts>(
    (n, c) => ({
      ads: n.ads + c.ads,
      ads_live: n.ads_live + c.ads_live,
      ads_spent: n.ads_spent + c.ads_spent,
    }),
    { ads: 0, ads_live: 0, ads_spent: 0 }
  );
  const node = adset ?? campaign;
  const counts: LibraryCounts = node ?? accountCounts;
  const title = adset ? adsetName(adset) : campaign ? campaignName(campaign) : 'All campaigns';

  const day = (iso: string) =>
    new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      timeZone: 'UTC',
    });
  const span = tree.window ? `${day(tree.window.start)} – ${day(tree.window.end)}` : 'Last 30 days';
  const x = (n: number | null) => (n == null ? '–' : `${n.toFixed(2)}x`);

  const stats = (t: GridRates) => [
    {
      label: 'Spend',
      value: fmt.money(t.spend),
      note: node && accountSpend > 0 ? `${Math.round((t.spend / accountSpend) * 100)}% of account` : null,
    },
    { label: 'ROAS', value: x(t.roas), note: null },
    {
      label: 'Cost / purchase',
      value: t.cpa == null ? '–' : fmt.money(t.cpa),
      note: null,
    },
    { label: 'Purchases', value: fmt.count(t.purchases), note: null },
    {
      label: 'Ads live now',
      value: `${counts.ads_live} of ${counts.ads}`,
      note: null,
    },
  ];
  const summary: GridRates | null = node ? node.totals : tree.totals;

  const treeEl = (
    <LibraryTree campaigns={tree.campaigns} campaignId={campaignId} adsetId={adsetId} delivery={delivery} keep={keep.toString()} currency={currency} />
  );
  const filtered = Boolean(sp.format || search);

  return (
    <div className="space-y-4">
      <RememberGridUrl />
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Campaigns</h1>
        <p className="shrink-0 text-sm text-muted-foreground sm:pt-1.5">
          {tree.window && <span className="mr-2 font-medium text-foreground">30d</span>}
          <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium tabular-nums">{span}</span>
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[17rem_minmax(0,1fr)] xl:grid-cols-[19rem_minmax(0,1fr)]">
        {/* Beside the grid from lg up; above it, folded, on smaller screens. */}
        <aside className="hidden lg:block">
          <div className="sticky top-6 flex max-h-[calc(100dvh-3rem)] flex-col">{treeEl}</div>
        </aside>
        <details className="group rounded-xl border border-border lg:hidden">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2.5 text-sm font-medium [&::-webkit-details-marker]:hidden">
            <span className="min-w-0 truncate">
              <span className="text-muted-foreground">Browse: </span>
              {title}
            </span>
            <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
          </summary>
          <div className="max-h-[60dvh] overflow-y-auto border-t border-border p-2">{treeEl}</div>
        </details>

        <section className="min-w-0 space-y-4" aria-labelledby="node-title">
          <div className="space-y-1">
            {campaign && (
              <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
                <Link href={`/campaigns${keep.size ? `?${keep}` : ''}`} className="hover:text-foreground hover:underline">
                  All campaigns
                </Link>
                {campaign && (
                  <>
                    <ChevronRight className="size-3" aria-hidden="true" />
                    {adset ? (
                      <Link
                        href={(() => {
                          const q = new URLSearchParams(keep);
                          q.set('campaign', campaignId!);
                          return `/campaigns?${q}`;
                        })()}
                        translate="no"
                        className="max-w-[40ch] truncate hover:text-foreground hover:underline"
                      >
                        {campaignName(campaign)}
                      </Link>
                    ) : (
                      <span translate="no" className="max-w-[40ch] truncate">
                        {campaignName(campaign)}
                      </span>
                    )}
                  </>
                )}
                {adset && (
                  <>
                    <ChevronRight className="size-3" aria-hidden="true" />
                    <span className="text-foreground">Ad set</span>
                  </>
                )}
              </nav>
            )}
            <h2 id="node-title" translate="no" className="text-lg leading-snug font-semibold [overflow-wrap:anywhere]">
              {title}
            </h2>
          </div>

          {summary && (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-xl border border-border px-4 py-3 sm:grid-cols-3 xl:grid-cols-5">
              {stats(summary).map((s) => (
                <div key={s.label} className="min-w-0">
                  <dt className="text-xs text-muted-foreground">{s.label}</dt>
                  <dd className="text-base font-semibold tabular-nums">
                    {s.value}
                    {s.note && <span className="block text-xs font-normal text-muted-foreground">{s.note}</span>}
                  </dd>
                </div>
              ))}
            </dl>
          )}

          <LibraryFilters counts={ads.counts} />
          <p className="hidden text-sm text-muted-foreground lg:block">
            Percentages are the gap to the other ads of the same format in this {scope === 'adset' ? 'ad set' : scope}.
          </p>

          {ads.items.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-16 text-center">
              <SearchX className="size-8 text-muted-foreground" aria-hidden="true" />
              <p className="max-w-sm text-sm text-muted-foreground">
                {filtered
                  ? 'No ads here match these filters.'
                  : delivery === 'live'
                    ? 'Nothing here is live now.'
                    : delivery === 'spent'
                      ? 'Nothing here delivered in these 30 days.'
                      : 'No ads here.'}
              </p>
              {delivery !== 'all' && ads.counts.all > 0 && (
                <Link href={pageHref({ delivery: 'all', page: null })} className="text-sm font-medium underline underline-offset-4 hover:no-underline">
                  Show all {ads.counts.all} ads
                </Link>
              )}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
                {ads.items.map((a, i) => (
                  <LibraryAdCard key={a.id} ad={a} fmt={fmt} scope={scope} sort={sort} eager={i < 8} />
                ))}
              </div>
              <Pagination page={page} totalPages={totalPages} makeHref={makeHref} />
            </>
          )}
        </section>
      </div>
    </div>
  );
}
