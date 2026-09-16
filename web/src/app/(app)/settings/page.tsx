import { AccountsPanel } from '@/components/accounts-panel';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Settings' };

export default async function Settings() {
  await requireAuth();
  const [me, accounts, connection] = await Promise.all([api.me(), api.accounts(), api.fivetranStatus()]);
  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Signed in as {me.user_id} · role {me.role} · org {me.org_id}
        </p>
      </div>
      <AccountsPanel accounts={accounts} connection={connection} />
    </div>
  );
}
