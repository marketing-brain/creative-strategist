import { AccountsPanel } from '@/components/accounts-panel';
import { BrandKitForm } from '@/components/brand-kit-form';
import { ApiError, api } from '@/lib/api';

export default async function Onboarding() {
  const accounts = await api.accounts();
  const kit = await api.brandKit().catch((e) => (e instanceof ApiError && e.status === 404 ? null : Promise.reject(e)));
  return <div className="mx-auto max-w-2xl space-y-10"><AccountsPanel accounts={accounts} /><BrandKitForm initial={kit} /></div>;
}
