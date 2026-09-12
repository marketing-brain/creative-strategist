'use client';
import { useRouter, useSearchParams } from 'next/navigation';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { Dimension } from '@/lib/types';

const formats = [
  { value: 'image', label: 'Image' },
  { value: 'video', label: 'Video' },
  { value: 'carousel', label: 'Carousel' },
  { value: 'unresolved', label: 'Unresolved' },
];

export function CreativeFilters({ accounts, dimensions }: { accounts: { id: string; name: string }[]; dimensions: Dimension[] }) {
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

  const filterableDimensions = dimensions.filter((d) => d.values);
  const selectedDimension = params.get('dimension') ?? '';
  const dimensionValues = filterableDimensions.find((d) => d.name === selectedDimension)?.values ?? [];

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
    </div>
  );
}
