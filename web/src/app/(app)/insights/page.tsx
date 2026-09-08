import { redirect } from 'next/navigation';
import { Sparkles } from 'lucide-react';
import { InsightCard } from '@/components/insight-card';
import { Pagination } from '@/components/pagination';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Insights' };

const PAGE_SIZE = 50;

export default async function Insights({ searchParams }: { searchParams: Promise<{ page?: string }> }) {
  await requireAuth();
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);
  const makeHref = (p: number) => (p > 1 ? `/insights?page=${p}` : '/insights');

  const { items: insights, total } = await api.insights('pattern', (page - 1) * PAGE_SIZE);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (page > totalPages) redirect(makeHref(totalPages));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
        <p className="text-sm text-muted-foreground">Patterns the nightly analysis found across your tagged, spending creatives.</p>
      </div>
      {insights.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-20 text-center">
          <Sparkles className="size-8 text-muted-foreground" aria-hidden="true" />
          <p className="max-w-sm text-sm text-muted-foreground">
            No insights yet. They appear after the nightly analysis once enough tagged creatives have spend.
          </p>
        </div>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            {insights.map((i) => (
              <InsightCard key={i.id} insight={i} />
            ))}
          </div>
          <Pagination page={page} totalPages={totalPages} makeHref={makeHref} />
        </>
      )}
    </div>
  );
}
