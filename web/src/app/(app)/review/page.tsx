import { ClipboardCheck } from 'lucide-react';
import { ReviewCard } from '@/components/review-card';
import { cn } from 'cn';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Review' };

const states = ['draft', 'approved', 'rejected'] as const;

export default async function Review({ searchParams }: { searchParams: Promise<{ state?: string }> }) {
  await requireAuth();
  const { state = 'draft' } = await searchParams;
  const assets = await api.reviewQueue(state);
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
          <p className="text-sm text-muted-foreground">Approve, reject, and download generated variants.</p>
        </div>
        <div className="inline-flex w-fit gap-1 rounded-lg bg-muted p-1">
          {states.map((s) => (
            <a
              key={s}
              href={`/review?state=${s}`}
              aria-current={s === state ? 'page' : undefined}
              className={cn(
                'rounded-md px-3 py-1 text-sm font-medium capitalize transition-colors',
                s === state ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
              )}
            >
              {s}
            </a>
          ))}
        </div>
      </div>
      {assets.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-16 text-center">
          <ClipboardCheck className="size-8 text-muted-foreground" aria-hidden="true" />
          <p className="max-w-sm text-sm text-muted-foreground">Nothing in the {state} queue right now.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-4">
          {assets.map((a) => (
            <ReviewCard key={a.id} asset={a} />
          ))}
        </div>
      )}
    </div>
  );
}
