'use client';
import { useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowDown, ArrowUp, Search } from 'lucide-react';
import { label, words } from '@/components/tag-list';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { Dimension } from '@/lib/types';

const formats = [
  { value: 'image', label: 'Image' },
  { value: 'video', label: 'Video' },
  { value: 'carousel', label: 'Carousel' },
  { value: 'unresolved', label: 'Media unavailable' },
];

// Meta mints a new creative id whenever an ad is copied, so the same file shows up many
// times. Footage pools those copies into one card; the other view lists every Meta creative.
const groups = [
  { value: 'footage', label: 'Grouped by file' },
  { value: 'creative', label: 'Every Meta creative' },
];

// Every sort reads the last 30 days. Rate sorts list ads with too little delivery to read
// after the rest, whichever way the arrow points. Cost per purchase opens cheapest first.
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

export function CreativeFilters({
  accounts,
  dimensions,
  openValues,
}: {
  accounts: { id: string; name: string }[];
  dimensions: Dimension[];
  openValues?: { value: string; count: number }[];
}) {
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
    router.push(`/creatives${s ? `?${s}` : ''}`);
  };

  const sort = params.get('sort') ?? 'spend';
  const naturalOrder = DEFAULT_ORDER[sort] ?? 'desc';
  const order = params.get('order') ?? naturalOrder;

  // The search box owns its text and pushes it to the URL after a pause in typing. It
  // follows the URL only when the URL changes underneath it, as Back does.
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
    // `update` closes over the current params; re-running on its identity would re-push.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  const filterableDimensions = dimensions.filter((d) => d.values || d.kind === 'open');
  const selectedDimension = params.get('dimension') ?? '';
  const selected = filterableDimensions.find((d) => d.name === selectedDimension);
  const dimensionValues = selected?.kind === 'open' ? (openValues ?? []).map((v) => v.value) : (selected?.values ?? []);

  return (
    // Two even columns on a phone, one wrapping row from there up.
    <div className="grid grid-cols-2 items-center gap-2 sm:flex sm:flex-wrap">
      <div className="relative col-span-2 min-w-48 sm:flex-1 sm:basis-56">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input
          type="search"
          name="q"
          autoComplete="off"
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search ad name, headline or creative id…"
          aria-label="Search creatives"
          className="pl-8"
        />
      </div>
      {accounts.length > 1 && (
        <Select value={params.get('account_id') ?? ''} onValueChange={(v) => update({ account_id: v || null })}>
          <SelectTrigger aria-label="Filter by account" className="w-full sm:w-40">
            <SelectValue placeholder="All accounts">{(v: string) => accounts.find((a) => a.id === v)?.name ?? 'All accounts'}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All accounts</SelectItem>
            {accounts.map((a) => (
              <SelectItem key={a.id} value={a.id}>
                {a.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <Select value={params.get('format') ?? ''} onValueChange={(v) => update({ format: v || null })}>
        <SelectTrigger aria-label="Filter by format" className="w-full sm:w-36">
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
      <Select value={selectedDimension} onValueChange={(v) => update({ dimension: v || null, value: null })}>
        <SelectTrigger aria-label="Filter by tag dimension" className="w-full sm:w-40">
          <SelectValue placeholder="Any tag">{(v: string) => (filterableDimensions.some((d) => d.name === v) ? label(v) : 'Any tag')}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">None</SelectItem>
          {filterableDimensions.map((d) => (
            <SelectItem key={d.name} value={d.name}>
              {label(d.name)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {selectedDimension && (
        <Select value={params.get('value') ?? ''} onValueChange={(v) => update({ value: v || null })}>
          <SelectTrigger aria-label="Filter by tag value" className="w-full sm:w-40">
            <SelectValue placeholder="Any value">{(v: string) => words(v)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {dimensionValues.map((v) => (
              <SelectItem key={v} value={v}>
                {words(v)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <Select value={params.get('group') ?? 'footage'} onValueChange={(v) => update({ group: v === 'footage' ? null : v })}>
        <SelectTrigger aria-label="Group creatives" className="w-full sm:w-48">
          <SelectValue>{(v: string) => groups.find((g) => g.value === v)?.label ?? 'Grouped by file'}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {groups.map((g) => (
            <SelectItem key={g.value} value={g.value}>
              {g.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <div className="flex min-w-0 gap-2 sm:contents">
        <Select value={sort} onValueChange={(v) => update({ sort: v === 'spend' ? null : v, order: null })}>
          <SelectTrigger aria-label="Sort by, over the last 30 days" className="min-w-0 flex-1 sm:w-48 sm:flex-none">
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
