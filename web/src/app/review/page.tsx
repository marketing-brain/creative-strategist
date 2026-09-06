import { ClipboardCheck } from 'lucide-react';
import { ReviewCard } from '@/components/review-card';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from 'cn';
import { api } from '@/lib/api';

export const metadata = { title: 'Review' };

const states = ['draft', 'approved', 'rejected'] as const;

export default async function Review({ searchParams }: { searchParams: Promise<{ state?: string }> }) {
  const { state = 'draft' } = await searchParams;
  const [assets, accounts, jobs, me] = await Promise.all([api.reviewQueue(state), api.accounts(), api.publishJobs(), api.me()]);
  const isAdmin = me.role === 'admin';
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
          <p className="text-sm text-muted-foreground">Approve, reject, and publish generated variants.</p>
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
            <ReviewCard key={a.id} asset={a} accounts={accounts.filter((x) => x.status === 'active')} isAdmin={isAdmin} />
          ))}
        </div>
      )}
      {jobs.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Publish jobs</h2>
          <Card size="sm">
            <CardHeader className="sr-only">
              <CardTitle>Publish jobs</CardTitle>
            </CardHeader>
            <CardContent className="divide-y divide-border p-0">
              {jobs.map((j) => (
                <div key={j.id} className="flex items-center justify-between px-4 py-2.5 text-sm">
                  <span className="truncate">{j.generated_asset_id}</span>
                  <span className="shrink-0 text-muted-foreground">
                    {j.status}
                    {j.platform_ad_id ? ` · ad ${j.platform_ad_id}` : ''}
                    {j.error ? ` · ${j.error}` : ''}
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}
