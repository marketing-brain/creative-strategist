'use client';
import { useRef, useState } from 'react';
import { Loader2, Plug } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, api } from '@/lib/api';
import type { Account } from '@/lib/types';

const statusVariant = { active: 'success', paused: 'secondary', disconnected: 'destructive' } as const;

type MsgKind = 'info' | 'success' | 'error';
const msgClass: Record<MsgKind, string> = {
  info: 'text-muted-foreground',
  success: 'text-emerald-600 dark:text-emerald-400',
  error: 'text-destructive',
};

export function AccountsPanel({ accounts }: { accounts: Account[] }) {
  const [msg, setMsg] = useState<{ text: string; kind: MsgKind } | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [needsDisconnect, setNeedsDisconnect] = useState(false);
  const messageListenerRef = useRef<((e: MessageEvent) => void) | null>(null);
  const closeWatcherRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopWatchingPopup = () => {
    if (closeWatcherRef.current) {
      clearInterval(closeWatcherRef.current);
      closeWatcherRef.current = null;
    }
  };

  const connect = async () => {
    // If a previous connect attempt never completed (popup closed manually, or the
    // Connect Card never sent the return message), drop its listener before adding a
    // new one — otherwise a stale listener stays live and double-finalizes on the
    // next successful return message, using its own (now-stale) connector_id.
    if (messageListenerRef.current) {
      window.removeEventListener('message', messageListenerRef.current);
      messageListenerRef.current = null;
    }
    stopWatchingPopup();
    setConnecting(true);
    setMsg(null);
    setNeedsDisconnect(false);
    try {
      const { connect_card_url, connector_id } = await api.fivetranConnectStart();
      const width = 520;
      const height = 720;
      const left = Math.round(window.screenX + Math.max(0, (window.outerWidth - width) / 2));
      const top = Math.round(window.screenY + Math.max(0, (window.outerHeight - height) / 2));
      const popup = window.open(
        connect_card_url,
        'fivetran-connect',
        `width=${width},height=${height},left=${left},top=${top},popup=1`,
      );
      if (!popup) {
        setConnecting(false);
        setMsg({
          text: 'Your browser blocked the connect window — allow pop-ups for this site and try again.',
          kind: 'error',
        });
        return;
      }
      setMsg({ text: 'Waiting for you to finish connecting in the popup…', kind: 'info' });

      const onMessage = async (e: MessageEvent) => {
        if (e.origin !== window.location.origin || e.data?.type !== 'fivetran:return') return;
        window.removeEventListener('message', onMessage);
        messageListenerRef.current = null;
        stopWatchingPopup();
        popup.close();
        try {
          await api.fivetranConnectFinalize(connector_id);
          setMsg({ text: 'Connected — accounts will appear once the first sync finishes.', kind: 'success' });
        } catch (err) {
          setMsg({
            text: `Failed to finish connecting: ${err instanceof Error ? err.message : 'Something went wrong.'}`,
            kind: 'error',
          });
        } finally {
          setConnecting(false);
        }
      };
      messageListenerRef.current = onMessage;
      window.addEventListener('message', onMessage);

      // The popup gives no other signal if the user just closes it without finishing —
      // without this, the button would stay stuck in "Connecting…" forever.
      closeWatcherRef.current = setInterval(() => {
        if (popup.closed) {
          stopWatchingPopup();
          window.removeEventListener('message', onMessage);
          messageListenerRef.current = null;
          setConnecting(false);
          setMsg({ text: 'Connect window closed before finishing — try again when ready.', kind: 'info' });
        }
      }, 500);
    } catch (e) {
      setConnecting(false);
      if (e instanceof ApiError && e.status === 403) {
        setMsg({ text: "You don't have permission to do this — ask an admin.", kind: 'error' });
      } else if (e instanceof ApiError && e.status === 409) {
        setMsg({
          text: 'A Meta connection already exists for this workspace. Disconnecting it will stop syncing until you reconnect — this cannot be undone from here.',
          kind: 'error',
        });
        setNeedsDisconnect(true);
      } else {
        setMsg({ text: `Failed to connect: ${e instanceof Error ? e.message : 'Something went wrong.'}`, kind: 'error' });
      }
    }
  };
  const disconnect = async () => {
    if (!window.confirm('Disconnect the existing Meta connection? This stops syncing until you reconnect.')) return;
    setConnecting(true);
    try {
      await api.fivetranDisconnect();
      setNeedsDisconnect(false);
      setMsg({ text: 'Disconnected — click Connect Meta to set up a new connection.', kind: 'info' });
    } catch (e) {
      setMsg({ text: `Failed to disconnect: ${e instanceof Error ? e.message : 'Something went wrong.'}`, kind: 'error' });
    } finally {
      setConnecting(false);
    }
  };
  const activate = async (id: string) => {
    try {
      const { job_id } = await api.activateAccount(id);
      setMsg({
        text: job_id ? 'Sync queued. Creatives will appear as they sync.' : 'Activated — connect Meta first to start syncing.',
        kind: 'success',
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setMsg({ text: "You don't have permission to do this — ask an admin.", kind: 'error' });
      } else {
        setMsg({ text: `Failed to activate: ${e instanceof Error ? e.message : 'Something went wrong.'}`, kind: 'error' });
      }
    }
  };
  const sync = async (id: string) => {
    try {
      await api.syncAccount(id);
      setMsg({ text: 'Sync queued.', kind: 'success' });
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setMsg({ text: "You don't have permission to do this — ask an admin.", kind: 'error' });
      } else {
        setMsg({ text: `Failed to sync: ${e instanceof Error ? e.message : 'Something went wrong.'}`, kind: 'error' });
      }
    }
  };
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Ad accounts</CardTitle>
        <Button size="sm" onClick={connect} disabled={connecting} className="gap-1.5">
          {connecting ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Plug className="size-4" aria-hidden="true" />}
          {connecting ? 'Connecting…' : 'Connect Meta'}
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
          <p role="status" className={`text-sm ${msgClass[msg.kind]}`}>
            {msg.text}
          </p>
        )}
        {needsDisconnect && (
          <Button size="sm" variant="destructive" onClick={disconnect} disabled={connecting}>
            Disconnect existing connection
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
