import { AccountReadSection } from '@/components/account-read';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Insights' };

export default async function Insights() {
  await requireAuth();
  const accounts = await api.accounts();
  const active = accounts.find((a) => a.status === 'active') ?? accounts[0];
  const accountRead = active ? await api.accountRead(active.id).catch(() => ({ status: 'none' as const })) : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
        <p className="text-sm text-muted-foreground">What this account&apos;s video creative does, what works, and why.</p>
      </div>
      {active && accountRead ? (
        <AccountReadSection data={accountRead} accountId={active.id} />
      ) : (
        <div className="rounded-xl border border-dashed border-border px-6 py-8 text-center text-sm text-muted-foreground">
          Connect an ad account to get an account read.
        </div>
      )}
    </div>
  );
}
