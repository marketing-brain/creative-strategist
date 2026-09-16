import { redirect } from 'next/navigation';
import { ImageOff } from 'lucide-react';
import { CreativeCard } from '@/components/creative-card';
import { CreativeFilters } from '@/components/creative-filters';
import { Pagination } from '@/components/pagination';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Creatives' };

const PAGE_SIZE = 50;

export default async function Creatives({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireAuth();
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);

  const makeQuery = (p: number) => {
    const q = new URLSearchParams();
    if (sp.account_id) q.set('account_id', sp.account_id);
    if (sp.format) q.set('format', sp.format);
    // GET /creatives 422s unless dimension and value arrive together; a dimension on its
    // own only selects which value dropdown to fetch.
    if (sp.dimension && sp.value) {
      q.set('dimension', sp.dimension);
      q.set('value', sp.value);
    }
    q.set('limit', String(PAGE_SIZE));
    q.set('offset', String((p - 1) * PAGE_SIZE));
    return q;
  };
  const makeHref = (p: number) => {
    const q = new URLSearchParams();
    if (sp.account_id) q.set('account_id', sp.account_id);
    if (sp.format) q.set('format', sp.format);
    if (sp.dimension && sp.value) {
      q.set('dimension', sp.dimension);
      q.set('value', sp.value);
    }
    if (p > 1) q.set('page', String(p));
    const s = q.toString();
    return `/creatives${s ? `?${s}` : ''}`;
  };

  const [{ items: creatives, total }, accounts, dimensions] = await Promise.all([
    api.creatives(`?${makeQuery(page)}`),
    api.accounts(),
    api.dimensions(),
  ]);
  const selectedDim = dimensions.find((d) => d.name === sp.dimension);
  const openValues = selectedDim?.kind === 'open' ? await api.dimensionValues(selectedDim.name) : undefined;

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (page > totalPages) redirect(makeHref(totalPages));

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Creatives</h1>
          <p className="text-sm text-muted-foreground">Every ad creative synced from your connected accounts.</p>
        </div>
        <CreativeFilters accounts={accounts} dimensions={dimensions} openValues={openValues} />
      </div>
      {creatives.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-20 text-center">
          <ImageOff className="size-8 text-muted-foreground" aria-hidden="true" />
          <p className="max-w-sm text-sm text-muted-foreground">
            No creatives match these filters yet. They will appear here once an account finishes syncing.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
            {creatives.map((c) => (
              <CreativeCard key={c.id} creative={c} />
            ))}
          </div>
          <Pagination page={page} totalPages={totalPages} makeHref={makeHref} />
        </>
      )}
    </div>
  );
}
