import { AccountReadSection } from '@/components/account-read';
import { buildAccountReadView } from '@/lib/account-read-view';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';
import type { AccountRead } from '@/lib/types';

export const metadata = { title: 'Insights' };

export default async function Insights() {
  await requireAuth();
  const accounts = await api.accounts();
  const active = accounts.find((a) => a.status === 'active') ?? accounts[0];
  const accountRead: AccountRead | null = active ? await api.accountRead(active.id).catch(() => ({ status: 'none' as const })) : null;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
        <p className="text-sm text-muted-foreground">
          What {active ? active.name : 'this account'}&apos;s video ads do, what works, and how sure that is.
        </p>
      </div>
      {active && accountRead ? (
        // Built on the server: the page ships as HTML with no client component.
        <AccountReadSection
          status={accountRead.status}
          nUnits={accountRead.n_units}
          view={buildAccountReadView(accountRead)}
          stale={Boolean(accountRead.newer_failed_error)}
          currency={active.currency}
        />
      ) : (
        <div className="rounded-xl border border-dashed border-border px-6 py-8 text-center text-sm text-muted-foreground">
          Connect an ad account to get an account read.
        </div>
      )}
    </div>
  );
}
