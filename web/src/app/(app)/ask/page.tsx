import { AccountReadAsk } from '@/components/account-read-ask';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Ask' };

export default async function Ask() {
  await requireAuth();
  const accounts = await api.accounts();
  const active = accounts.find((a) => a.status === 'active') ?? accounts[0];
  // Questions are answered against a stored account read, so /insights and this page
  // resolve the same account and fetch the same run.
  const accountRead = active ? await api.accountRead(active.id).catch(() => ({ status: 'none' as const })) : null;
  // Evidence rows resolve file names out of units, so the run has to have finished.
  const ready = accountRead && 'units' in accountRead && accountRead.units?.length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Ask</h1>
        <p className="text-sm text-muted-foreground">
          Put a question to this account&apos;s creative. Answers come from graded comparisons, with the evidence attached.
        </p>
      </div>
      {active && ready ? (
        <AccountReadAsk accountId={active.id} tests={accountRead.tests ?? []} units={accountRead.units ?? []} />
      ) : (
        <div className="rounded-xl border border-dashed border-border px-6 py-8 text-center text-sm text-muted-foreground">
          {active
            ? 'No account read yet. Questions are answered against one, so this page opens once the first read finishes.'
            : 'Connect an ad account to ask questions about its creative.'}
        </div>
      )}
    </div>
  );
}
