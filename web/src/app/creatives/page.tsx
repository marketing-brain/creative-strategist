import { ImageOff } from 'lucide-react';
import { CreativeCard } from '@/components/creative-card';
import { CreativeFilters } from '@/components/creative-filters';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Creatives' };

export default async function Creatives({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireAuth();
  const sp = await searchParams;
  const q = new URLSearchParams();
  if (sp.account_id) q.set('account_id', sp.account_id);
  if (sp.format) q.set('format', sp.format);
  const [creatives, accounts] = await Promise.all([api.creatives(q.toString() ? `?${q}` : ''), api.accounts()]);
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Creatives</h1>
          <p className="text-sm text-muted-foreground">Every ad creative synced from your connected accounts.</p>
        </div>
        <CreativeFilters accounts={accounts} />
      </div>
      {creatives.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-20 text-center">
          <ImageOff className="size-8 text-muted-foreground" aria-hidden="true" />
          <p className="max-w-sm text-sm text-muted-foreground">
            No creatives match these filters yet. They will appear here once an account finishes syncing.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
          {creatives.map((c) => (
            <CreativeCard key={c.id} creative={c} />
          ))}
        </div>
      )}
    </div>
  );
}
