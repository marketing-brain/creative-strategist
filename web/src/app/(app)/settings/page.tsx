import { MetaSyncSettings } from '@/components/meta-sync-settings';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Settings' };

export default async function Settings() {
  await requireAuth();
  const [me, accounts, sync] = await Promise.all([api.me(), api.accounts(), api.syncStatus()]);
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">Where your Meta data comes from and how current it is.</p>
      </div>
      <MetaSyncSettings initial={sync} accounts={accounts} role={me.role} />
    </div>
  );
}
