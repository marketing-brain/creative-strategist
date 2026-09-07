'use client';
import { useState } from 'react';
import { Plug } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, api } from '@/lib/api';
import type { Account } from '@/lib/types';

const statusVariant = { active: 'success', paused: 'secondary', disconnected: 'destructive' } as const;

export function AccountsPanel({ accounts }: { accounts: Account[] }) {
  const [msg, setMsg] = useState<string | null>(null);
  const connect = async () => {
    try {
      const { connect_card_url, connector_id } = await api.fivetranConnectStart();
      const popup = window.open(connect_card_url, 'fivetran-connect', 'width=500,height=700');
      const onMessage = async (e: MessageEvent) => {
        if (e.origin !== window.location.origin || e.data?.type !== 'fivetran:return') return;
        window.removeEventListener('message', onMessage);
        popup?.close();
        try {
          await api.fivetranConnectFinalize(connector_id);
          setMsg('Connected — accounts will appear once the first sync finishes.');
        } catch (err) {
          setMsg(`Failed to finish connecting: ${err instanceof Error ? err.message : 'Something went wrong.'}`);
        }
      };
      window.addEventListener('message', onMessage);
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
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Ad accounts</CardTitle>
        <Button size="sm" onClick={connect} className="gap-1.5">
          <Plug className="size-4" aria-hidden="true" />
          Connect Meta
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {accounts.length === 0 ? (
          <p className="text-sm text-muted-foreground">No accounts yet. Connect Meta to pick accounts.</p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {accounts.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-3 p-3 text-sm">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 font-medium">
                    <span className="truncate">{a.name}</span>
                    <span className="shrink-0 text-muted-foreground">({a.platform_account_id})</span>
                  </div>
                  <div className="flex items-center gap-1.5 text-muted-foreground">
                    <Badge variant={statusVariant[a.status]} className="capitalize">
                      {a.status}
                    </Badge>
                    {a.connection_status === 'error' && (
                      <Badge variant="destructive">{a.connection_error ?? 'Sync error'}</Badge>
                    )}
                    {a.sync_cursor?.last_synced_until ? `synced through ${String(a.sync_cursor.last_synced_until)}` : null}
                  </div>
                </div>
                {a.status === 'paused' ? (
                  <Button size="sm" onClick={() => activate(a.id)} className="shrink-0">
                    Activate
                  </Button>
                ) : (
                  <Button size="sm" variant="outline" onClick={() => sync(a.id)} className="shrink-0">
                    Sync now
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {msg && (
          <p role="status" className="text-sm">
            {msg}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
