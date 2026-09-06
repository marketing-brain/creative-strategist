'use client';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { ApiError, api } from '@/lib/api';
import type { Account } from '@/lib/types';

export function AccountsPanel({ accounts }: { accounts: Account[] }) {
  const [msg, setMsg] = useState<string | null>(null);
  const connect = async () => {
    try {
      const { url } = await api.metaOauthStart();
      window.location.assign(url);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setMsg("You don't have permission to do this — ask an admin.");
      } else {
        setMsg(`Failed to connect: ${e instanceof Error ? e.message : 'Something went wrong.'}`);
      }
    }
  };
  const activate = async (id: string) => {
    try {
      await api.activateAccount(id);
      setMsg('Backfill queued. Creatives will appear as they sync.');
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setMsg("You don't have permission to do this — ask an admin.");
      } else {
        setMsg(`Failed to activate: ${e instanceof Error ? e.message : 'Something went wrong.'}`);
      }
    }
  };
  const sync = async (id: string) => {
    try {
      await api.syncAccount(id);
      setMsg('Sync queued.');
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setMsg("You don't have permission to do this — ask an admin.");
      } else {
        setMsg(`Failed to sync: ${e instanceof Error ? e.message : 'Something went wrong.'}`);
      }
    }
  };
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Ad accounts</h2><Button onClick={connect}>Connect Meta</Button></div>
      {accounts.length === 0 && <p className="text-sm text-muted-foreground">No accounts yet. Connect Meta to pick accounts.</p>}
      <ul className="divide-y rounded border">
        {accounts.map((a) => (
          <li key={a.id} className="flex items-center justify-between p-3 text-sm">
            <div><div className="font-medium">{a.name} <span className="text-muted-foreground">({a.platform_account_id})</span></div>
              <div className="text-muted-foreground">{a.status}{a.sync_cursor?.last_synced_until ? ` · synced through ${String(a.sync_cursor.last_synced_until)}` : ''}</div></div>
            {a.status === 'paused' ? <Button size="sm" onClick={() => activate(a.id)}>Activate</Button> : <Button size="sm" variant="outline" onClick={() => sync(a.id)}>Sync now</Button>}
          </li>
        ))}
      </ul>
      {msg && <p role="status" className="text-sm">{msg}</p>}
    </section>
  );
}
