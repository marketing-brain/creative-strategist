'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowDown, ArrowUp, Search } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { LibraryDelivery } from '@/lib/types';

const formats = [
  { value: 'image', label: 'Image' },
  { value: 'video', label: 'Video' },
  { value: 'carousel', label: 'Carousel' },
  { value: 'unresolved', label: 'Media unavailable' },
];

// Same sorts as the creatives grid, read off one ad.
const sorts = [
  { value: 'spend', label: 'Spend' },
  { value: 'roas', label: 'ROAS' },
  { value: 'cpa', label: 'Cost per purchase' },
  { value: 'purchases', label: 'Purchases' },
  { value: 'hook_rate', label: 'Hook rate' },
  { value: 'link_ctr', label: 'Link CTR' },
  { value: 'impressions', label: 'Impressions' },
  { value: 'recent', label: 'Newest' },
];
const DEFAULT_ORDER: Record<string, 'asc' | 'desc'> = { cpa: 'asc' };

// Spent is the default because every number on the page covers those 30 days; Live is what
// Meta is delivering now, whatever it spent.
const deliveries: { value: LibraryDelivery; label: string; hint: string }[] = [
  {
    value: 'spent',
    label: 'Spent',
    hint: 'Ads with delivery in these 30 days',
  },
  {
    value: 'live',
    label: 'Live now',
    hint: 'Ads Meta is delivering now: the ad, its ad set and its campaign are all on',
  },
  {
    value: 'all',
    label: 'All',
    hint: 'Every ad, including paused, archived and deleted',
  },
];

export function LibraryFilters({ counts }: { counts: Record<LibraryDelivery, number> }) {
  const router = useRouter();
  const params = useSearchParams();

  const update = (updates: Record<string, string | null>) => {
    const q = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) q.set(key, value);
      else q.delete(key);
    }
    q.delete('page');
    const s = q.toString();
    router.push(`/campaigns${s ? `?${s}` : ''}`, { scroll: false });
  };

  const delivery = (params.get('delivery') ?? 'spent') as LibraryDelivery;
  const sort = params.get('sort') ?? 'spend';
  const naturalOrder = DEFAULT_ORDER[sort] ?? 'desc';
  const order = params.get('order') ?? naturalOrder;

  // Pushed to the URL after a pause in typing; follows the URL when Back changes it.
  const urlQuery = params.get('q') ?? '';
  const [text, setText] = useState(urlQuery);
  const pushed = useRef(urlQuery);
  useEffect(() => {
    if (urlQuery !== pushed.current) {
      pushed.current = urlQuery;
      setText(urlQuery);
    }
  }, [urlQuery]);
  useEffect(() => {
    const next = text.trim();
    if (next === pushed.current) return;
    const timer = setTimeout(() => {
      pushed.current = next;
      update({ q: next || null });
    }, 350);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
      <div role="radiogroup" aria-label="Which ads to list" className="inline-flex w-full rounded-lg border border-border bg-muted/40 p-0.5 sm:w-auto">
        {deliveries.map((d) => {
          const on = delivery === d.value;
          return (
            <button
              key={d.value}
              type="button"
              role="radio"
              aria-checked={on}
              title={d.hint}
              onClick={() => update({ delivery: d.value === 'spent' ? null : d.value })}
              className={cn(
                'flex flex-1 items-center justify-center gap-1.5 rounded-md px-2.5 py-1 text-sm font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:flex-none',
                on ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
              )}
            >
              {d.value === 'live' && <span className="size-1.5 rounded-full bg-emerald-500" aria-hidden="true" />}
              {d.label}
              <span className="text-xs text-muted-foreground tabular-nums">{counts[d.value].toLocaleString('en-US')}</span>
            </button>
          );
        })}
      </div>
      <div className="relative min-w-48 sm:flex-1 sm:basis-48">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input
          type="search"
          autoComplete="off"
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search ad names…"
          aria-label="Search ads by name"
          className="pl-8"
        />
      </div>
      <div className="flex min-w-0 gap-2">
        <Select value={params.get('format') ?? ''} onValueChange={(v) => update({ format: v || null })}>
          <SelectTrigger aria-label="Filter by format" className="min-w-0 flex-1 sm:w-36 sm:flex-none">
            <SelectValue placeholder="All formats">{(v: string) => formats.find((f) => f.value === v)?.label ?? 'All formats'}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All formats</SelectItem>
            {formats.map((f) => (
              <SelectItem key={f.value} value={f.value}>
                {f.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(v) => update({ sort: v === 'spend' ? null : v, order: null })}>
          <SelectTrigger aria-label="Sort by, over the last 30 days" className="min-w-0 flex-1 sm:w-44 sm:flex-none">
            <SelectValue>{(v: string) => `Sort: ${sorts.find((s) => s.value === v)?.label ?? 'Spend'}`}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {sorts.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="icon"
          aria-label={order === 'desc' ? 'Sorted high to low, switch to low to high' : 'Sorted low to high, switch to high to low'}
          onClick={() => {
            const flipped = order === 'desc' ? 'asc' : 'desc';
            update({ order: flipped === naturalOrder ? null : flipped });
          }}
        >
          {order === 'desc' ? <ArrowDown aria-hidden="true" /> : <ArrowUp aria-hidden="true" />}
        </Button>
      </div>
    </div>
  );
}
