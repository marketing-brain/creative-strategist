'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

// Which ad set the read is about. Lives in the URL (?adset=), so a read can be linked to.
export function AdSetPicker({ adsets, value }: { adsets: { label: string; files: number }[]; value: string | null }) {
  const router = useRouter();
  const params = useSearchParams();
  return (
    <Select
      value={value ?? ''}
      onValueChange={(v) => {
        const q = new URLSearchParams(params.toString());
        if (v) q.set('adset', v);
        else q.delete('adset');
        router.push(`?${q}`, { scroll: false });
      }}
    >
      <SelectTrigger aria-label="Ad set to read" className="w-full max-w-xl">
        <SelectValue placeholder="Pick an ad set…">{(v: string) => v || 'Pick an ad set…'}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        {adsets.map((a) => (
          <SelectItem key={a.label} value={a.label}>
            <span translate="no">{a.label}</span>
            <span className="ml-2 text-xs text-muted-foreground tabular-nums">
              {a.files} file{a.files === 1 ? '' : 's'}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
