import { AccountsPanel } from '@/components/accounts-panel';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Get started' };

export default async function Onboarding() {
  await requireAuth();
  const [accounts, connection] = await Promise.all([api.accounts(), api.fivetranStatus()]);
  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Let’s get set up</h1>
        <p className="text-sm text-muted-foreground">Connect an ad account to start seeing creatives and insights.</p>
      </div>
      <AccountsPanel accounts={accounts} connection={connection} />
    </div>
  );
}
