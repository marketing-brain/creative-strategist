import { MetaSyncSettings } from '@/components/meta-sync-settings';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Get started' };

export default async function Onboarding() {
  await requireAuth();
  const [me, accounts, sync] = await Promise.all([api.me(), api.accounts(), api.syncStatus()]);
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Let’s get set up</h1>
        <p className="text-sm text-muted-foreground">Connect Meta, then include the ad accounts you want to see.</p>
      </div>
      <MetaSyncSettings initial={sync} accounts={accounts} role={me.role} />
    </div>
  );
}
