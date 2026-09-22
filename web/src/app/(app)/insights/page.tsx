import Link from 'next/link';

import { AccountReadSection } from '@/components/account-read';
import { buildAccountReadView } from '@/lib/account-read-view';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';
import type { AccountRead } from '@/lib/types';

export const metadata = { title: 'Insights' };

const FORMATS = [
  { value: 'video', label: 'Video', href: '/insights' },
  { value: 'image', label: 'Image', href: '/insights?format=image' },
] as const;

export default async function Insights({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireAuth();
  const format = (await searchParams).format === 'image' ? 'image' : 'video';
  const accounts = await api.accounts();
  const active = accounts.find((a) => a.status === 'active') ?? accounts[0];
  // Meta accounts synced without a display name carry their numeric id as the name.
  const accountLabel = active && !/^\d+$/.test(active.name.trim()) ? active.name : 'this account';
  // The account read covers video only; the image read is not built yet.
  const accountRead: AccountRead | null =
    active && format === 'video' ? await api.accountRead(active.id).catch(() => ({ status: 'none' as const })) : null;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
          <p className="text-sm text-muted-foreground">
            What {accountLabel}&apos;s {format} ads do, what works, and how sure that is.
          </p>
        </div>
        <nav aria-label="Ad format" className="inline-flex h-8 items-center rounded-lg bg-muted p-[3px] text-sm font-medium">
          {FORMATS.map((f) => (
            <Link
              key={f.value}
              href={f.href}
              aria-current={f.value === format ? 'page' : undefined}
              className={
                f.value === format
                  ? 'inline-flex h-full items-center rounded-md bg-background px-3 text-foreground shadow-sm dark:border dark:border-input dark:bg-input/30'
                  : 'inline-flex h-full items-center rounded-md px-3 text-foreground/60 transition-colors hover:text-foreground dark:text-muted-foreground dark:hover:text-foreground'
              }
            >
              {f.label}
            </Link>
          ))}
        </nav>
      </div>
      {format === 'image' ? (
        <div className="rounded-xl border border-dashed border-border px-6 py-8 text-center text-sm text-muted-foreground">
          Image insights are coming soon.
        </div>
      ) : active && accountRead ? (
        // Built on the server: the page ships as HTML with no client component.
        <AccountReadSection
          status={accountRead.status}
          nUnits={accountRead.n_units}
          view={buildAccountReadView(accountRead)}
          coverage={accountRead.coverage}
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
