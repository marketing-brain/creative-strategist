'use client';
import { useRouter, useSearchParams } from 'next/navigation';

export function CreativeFilters({ accounts }: { accounts: { id: string; name: string }[] }) {
  const router = useRouter();
  const params = useSearchParams();
  const update = (key: string, value: string) => {
    const q = new URLSearchParams(params.toString());
    if (value) q.set(key, value); else q.delete(key);
    const s = q.toString();
    router.push(`/creatives${s ? `?${s}` : ''}`);
  };
  return (
    <div className="flex gap-3 text-sm">
      <label>Account <select className="ml-1 rounded border p-1" value={params.get('account_id') ?? ''} onChange={(e) => update('account_id', e.target.value)}>
        <option value="">All</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label>Format <select className="ml-1 rounded border p-1" value={params.get('format') ?? ''} onChange={(e) => update('format', e.target.value)}>
        <option value="">All</option><option value="image">Image</option><option value="video">Video</option><option value="carousel">Carousel</option></select></label>
    </div>
  );
}
