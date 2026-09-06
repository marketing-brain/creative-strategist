import { AccountsPanel } from '@/components/accounts-panel';
import { BrandKitForm } from '@/components/brand-kit-form';
import { ApiError, api } from '@/lib/api';

export const metadata = { title: 'Get started' };

export default async function Onboarding() {
  const accounts = await api.accounts();
  const kit = await api.brandKit().catch((e) => (e instanceof ApiError && e.status === 404 ? null : Promise.reject(e)));
  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Let’s get set up</h1>
        <p className="text-sm text-muted-foreground">Connect an ad account and, optionally, your brand kit to start seeing creatives and insights.</p>
      </div>
      <AccountsPanel accounts={accounts} />
      <BrandKitForm initial={kit} />
    </div>
  );
}
