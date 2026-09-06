import { AccountsPanel } from '@/components/accounts-panel';
import { BrandKitForm } from '@/components/brand-kit-form';
import { ApiError, api } from '@/lib/api';

export const metadata = { title: 'Settings' };

export default async function Settings() {
  const [me, accounts] = await Promise.all([api.me(), api.accounts()]);
  const kit = await api.brandKit().catch((e) => (e instanceof ApiError && e.status === 404 ? null : Promise.reject(e)));
  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Signed in as {me.user_id} · role {me.role} · org {me.org_id}
        </p>
      </div>
      <AccountsPanel accounts={accounts} />
      <BrandKitForm initial={kit} />
    </div>
  );
}
