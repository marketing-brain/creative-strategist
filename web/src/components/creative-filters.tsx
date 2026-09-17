'use client';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { Dimension } from '@/lib/types';

const formats = [
  { value: 'image', label: 'Image' },
  { value: 'video', label: 'Video' },
  { value: 'carousel', label: 'Carousel' },
  { value: 'unresolved', label: 'Unresolved' },
];

const sorts = [
  { value: 'recent', label: 'Newest' },
  { value: 'spend', label: 'Spend (30d)' },
  { value: 'impressions', label: 'Impressions (30d)' },
  { value: 'ctr', label: 'CTR (30d)' },
];

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

  const sort = params.get('sort') ?? 'recent';
  const order = params.get('order') ?? 'desc';
  const filterableDimensions = dimensions.filter((d) => d.values || d.kind === 'open');
  const selectedDimension = params.get('dimension') ?? '';
  const selected = filterableDimensions.find((d) => d.name === selectedDimension);
  const dimensionValues = selected?.kind === 'open' ? (openValues ?? []).map((v) => v.value) : (selected?.values ?? []);

  return (
    <div className="flex flex-wrap gap-2">
      <Select value={params.get('account_id') ?? ''} onValueChange={(v) => update({ account_id: v || null })}>
        <SelectTrigger aria-label="Filter by account" className="w-40">
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
      <Select value={params.get('format') ?? ''} onValueChange={(v) => update({ format: v || null })}>
        <SelectTrigger aria-label="Filter by format" className="w-36">
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
        <SelectTrigger aria-label="Filter by tag dimension" className="w-40">
          <SelectValue placeholder="Any tag">{(v: string) => filterableDimensions.find((d) => d.name === v)?.name ?? 'Any tag'}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">None</SelectItem>
          {filterableDimensions.map((d) => (
            <SelectItem key={d.name} value={d.name}>
              {d.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {selectedDimension && (
        <Select value={params.get('value') ?? ''} onValueChange={(v) => update({ value: v || null })}>
          <SelectTrigger aria-label="Filter by tag value" className="w-40">
            <SelectValue placeholder="Any value">{(v: string) => v}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {dimensionValues.map((v) => (
              <SelectItem key={v} value={v}>
                {v}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <Select value={sort} onValueChange={(v) => update({ sort: v === 'recent' ? null : v })}>
        <SelectTrigger aria-label="Sort by" className="w-44">
          <SelectValue>{(v: string) => sorts.find((s) => s.value === v)?.label ?? 'Newest'}</SelectValue>
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
        onClick={() => update({ order: order === 'desc' ? 'asc' : null })}
      >
        {order === 'desc' ? <ArrowDown aria-hidden="true" /> : <ArrowUp aria-hidden="true" />}
      </Button>
    </div>
  );
}
