'use client';
import { useRouter, useSearchParams } from 'next/navigation';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

const formats = [
  { value: 'image', label: 'Image' },
  { value: 'video', label: 'Video' },
  { value: 'carousel', label: 'Carousel' },
];

export function CreativeFilters({ accounts }: { accounts: { id: string; name: string }[] }) {
  const router = useRouter();
  const params = useSearchParams();

  const update = (key: string, value: string | null) => {
    const q = new URLSearchParams(params.toString());
    if (value) q.set(key, value);
    else q.delete(key);
    const s = q.toString();
    router.push(`/creatives${s ? `?${s}` : ''}`);
  };

  return (
    <div className="flex flex-wrap gap-2">
      <Select value={params.get('account_id') ?? ''} onValueChange={(v) => update('account_id', v)}>
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
      <Select value={params.get('format') ?? ''} onValueChange={(v) => update('format', v)}>
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
    </div>
  );
}
