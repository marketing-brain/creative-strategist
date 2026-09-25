'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ChevronRight, Layers3, Search } from 'lucide-react';
import { cn } from 'cn';
import { Input } from '@/components/ui/input';
import { adsetName, campaignName, inView } from '@/lib/campaigns';
import type { LibraryAdSet, LibraryCampaign, LibraryCounts, LibraryDelivery } from '@/lib/types';

function LiveDot({ n }: { n: LibraryCounts }) {
  const live = n.ads_live > 0;
  return (
    <span
      className={cn('size-2 shrink-0 rounded-full', live ? 'bg-emerald-500' : 'border border-muted-foreground/50')}
      title={live ? `${n.ads_live} of ${n.ads} ads live now` : 'Nothing live now'}
      aria-label={live ? `${n.ads_live} of ${n.ads} ads live now` : 'Nothing live now'}
      role="img"
    />
  );
}

export function LibraryTree({
  campaigns,
  campaignId,
  adsetId,
  delivery,
  keep,
  currency,
}: {
  campaigns: LibraryCampaign[];
  campaignId: string | null;
  adsetId: string | null;
  delivery: LibraryDelivery;
  // Query string the tree's links carry forward (delivery, sort, format...), without the node.
  keep: string;
  currency: string;
}) {
  const [filter, setFilter] = useState('');
  // The selected campaign starts open; the reader opens others by hand.
  const [open, setOpen] = useState<Set<string>>(() => new Set(campaignId ? [campaignId] : []));
  // Moving to another campaign (from the grid, a breadcrumb, Back) opens it too.
  useEffect(() => {
    if (campaignId) setOpen((prev) => (prev.has(campaignId) ? prev : new Set(prev).add(campaignId)));
  }, [campaignId]);
  // Built by hand: Node's and the browser's compact notation disagree ($191.0K vs $191K),
  // which breaks hydration.
  const spend = useMemo(() => {
    let symbol = '';
    try {
      symbol = new Intl.NumberFormat('en-US', { style: 'currency', currency }).formatToParts(0).find((p) => p.type === 'currency')?.value ?? '';
    } catch {
      /* unknown currency code: a bare number */
    }
    const short = (v: number) => (v >= 100 ? String(Math.round(v)) : String(Math.round(v * 10) / 10));
    return (n: number) => {
      if (n <= 0) return '–';
      if (n >= 1e6) return `${symbol}${short(n / 1e6)}M`;
      if (n >= 1e3) return `${symbol}${short(n / 1e3)}K`;
      return `${symbol}${Math.round(n)}`;
    };
  }, [currency]);

  const href = (c: string | null, s: string | null) => {
    const q = new URLSearchParams(keep);
    if (c) q.set('campaign', c);
    if (s) q.set('adset', s);
    const str = q.toString();
    return `/campaigns${str ? `?${str}` : ''}`;
  };

  const needle = filter.trim().toLowerCase();
  const shown = campaigns.filter((c) => inView(c, delivery) || c.id === campaignId);
  const hidden = campaigns.length - shown.length;
  // A name match on a campaign shows all its ad sets; a match on an ad set shows the
  // campaign open with just the matching ones.
  const rows = shown
    .map((c) => {
      const sets = c.adsets.filter((s) => inView(s, delivery) || s.id === adsetId);
      if (!needle) return { c, sets, forced: false };
      if (campaignName(c).toLowerCase().includes(needle)) return { c, sets, forced: false };
      const hits = sets.filter((s) => adsetName(s).toLowerCase().includes(needle));
      return hits.length ? { c, sets: hits, forced: true } : null;
    })
    .filter((r): r is { c: LibraryCampaign; sets: LibraryAdSet[]; forced: boolean } => r !== null);

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const rowClass = (selected: boolean) =>
    cn(
      'flex min-w-0 flex-1 items-center gap-2 rounded-md py-1.5 pr-2 text-left text-[13px] leading-snug transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
      selected ? 'bg-accent font-medium text-accent-foreground' : 'text-foreground/80 hover:bg-accent/60 hover:text-foreground'
    );

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Find a campaign or ad set…"
          aria-label="Find a campaign or ad set"
          autoComplete="off"
          spellCheck={false}
          className="pl-8"
        />
      </div>
      <nav aria-label="Campaigns and ad sets" className="min-h-0 overflow-y-auto">
        <ul className="flex flex-col gap-0.5">
          <li>
            <Link href={href(null, null)} aria-current={!campaignId ? 'page' : undefined} className={cn(rowClass(!campaignId), 'pl-2')}>
              <Layers3 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="flex-1">All campaigns</span>
              <span className="text-xs text-muted-foreground tabular-nums">{shown.length}</span>
            </Link>
          </li>
          {rows.map(({ c, sets, forced }) => {
            const key = c.id ?? '';
            const expanded = forced || open.has(key);
            const selected = campaignId === key && !adsetId;
            return (
              <li key={key}>
                <div className="flex items-start">
                  <button
                    type="button"
                    onClick={() => toggle(key)}
                    aria-expanded={expanded}
                    aria-label={`${expanded ? 'Collapse' : 'Expand'} ${campaignName(c)}`}
                    className="mt-1 flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <ChevronRight className={cn('size-3.5 transition-transform', expanded && 'rotate-90')} aria-hidden="true" />
                  </button>
                  <Link href={href(c.id, null)} aria-current={selected ? 'page' : undefined} className={cn(rowClass(selected), 'pl-1')} title={campaignName(c)}>
                    <LiveDot n={c} />
                    <span className="line-clamp-2 flex-1 [overflow-wrap:anywhere]">{campaignName(c)}</span>
                    <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{spend(c.totals.spend)}</span>
                  </Link>
                </div>
                {expanded && (
                  <ul className="mt-0.5 ml-6 flex flex-col gap-0.5 border-l border-border pl-1.5">
                    {sets.map((s) => {
                      const sel = campaignId === key && adsetId === s.id;
                      return (
                        <li key={s.id ?? ''}>
                          <Link href={href(c.id, s.id)} aria-current={sel ? 'page' : undefined} className={cn(rowClass(sel), 'pl-2')} title={adsetName(s)}>
                            <LiveDot n={s} />
                            <span className="line-clamp-2 flex-1 [overflow-wrap:anywhere]">{adsetName(s)}</span>
                            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{spend(s.totals.spend)}</span>
                          </Link>
                        </li>
                      );
                    })}
                    {sets.length === 0 && <li className="px-2 py-1 text-xs text-muted-foreground">No ad sets match.</li>}
                  </ul>
                )}
              </li>
            );
          })}
          {rows.length === 0 && <li className="px-2 py-3 text-sm text-muted-foreground">Nothing matches “{filter.trim()}”.</li>}
        </ul>
        {hidden > 0 && !needle && (
          <p className="mt-3 px-2 text-xs text-muted-foreground">
            {hidden} {hidden === 1 ? 'campaign' : 'campaigns'} hidden with {delivery === 'live' ? 'nothing live now' : 'no delivery in these 30 days'}.{' '}
            <Link
              href={(() => {
                const q = new URLSearchParams(keep);
                q.set('delivery', 'all');
                return `/campaigns?${q}`;
              })()}
              className="font-medium text-foreground underline underline-offset-4 hover:no-underline"
            >
              Show all
            </Link>
          </p>
        )}
      </nav>
    </div>
  );
}
