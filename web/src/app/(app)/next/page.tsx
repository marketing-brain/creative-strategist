import { NextPlanSection } from '@/components/next-plan';
import { buildNextView } from '@/lib/next-view';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';
import type { NextPlan } from '@/lib/types';

export const metadata = { title: 'Next' };

export default async function Next() {
  await requireAuth();
  const accounts = await api.accounts();
  const active = accounts.find((a) => a.status === 'active') ?? accounts[0];
  const plan: NextPlan | null = active ? await api.nextPlan(active.id).catch(() => ({ status: 'none' as const })) : null;

  return (
    <div className="space-y-8">
      <div className="max-w-5xl">
        <h1 className="text-2xl font-semibold tracking-tight">Next</h1>
        <p className="text-sm text-muted-foreground">What to change, what to make, and what this account has never tried.</p>
      </div>
      {active && plan?.status === 'done' ? (
        // Built on the server from a stored payload: the page ships as HTML with no client component.
        <NextPlanSection view={buildNextView(plan)} currency={active.currency} />
      ) : (
        <div className="rounded-xl border border-dashed border-border px-6 py-8 text-center text-sm text-muted-foreground">
          {!active
            ? 'Connect an ad account to get a plan.'
            : plan?.status === 'failed'
              ? 'The last plan did not finish. The previous one is shown once it is rebuilt.'
              : 'No plan yet. One is built after this account’s first account read finishes.'}
        </div>
      )}
    </div>
  );
}
