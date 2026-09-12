'use client';
import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertCircle, Loader2, Plug, RefreshCw, Unplug } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, api } from '@/lib/api';
import type { Account, MetaConnection } from '@/lib/types';

const statusVariant = { active: 'success', paused: 'secondary', disconnected: 'destructive' } as const;

type MsgKind = 'info' | 'success' | 'error';
const msgClass: Record<MsgKind, string> = {
  info: 'text-muted-foreground',
  success: 'text-emerald-600 dark:text-emerald-400',
  error: 'text-destructive',
};

// The connection has exactly one state that matters at any moment, derived from the
// meta_connection row itself -- never from whether a previous action happened to fail.
// 'error' also covers a connection someone deliberately disconnected: the backend has no
// separate terminal state for that today, it reuses 'error' with a specific message.
type ConnState = 'none' | 'pending' | 'syncing' | 'ready' | 'error';

function connState(connection: MetaConnection): ConnState {
  if (!connection.connected) return 'none';
  return connection.status ?? 'ready';
}

const statusCopy: Record<ConnState, { badge: string; variant: 'secondary' | 'success' | 'destructive'; description: (c: MetaConnection) => string }> = {
  none: {
    badge: 'Not connected',
    variant: 'secondary',
    description: () => 'Connect your Meta ad account to start syncing creatives and performance.',
  },
  pending: {
    badge: 'Setting up',
    variant: 'secondary',
    description: () => "Fivetran is running its first sync. This can take a few minutes.",
  },
  syncing: {
    badge: 'Syncing',
    variant: 'secondary',
    description: () => 'Refreshing the latest data from Meta.',
  },
  ready: {
    badge: 'Connected',
    variant: 'success',
    description: (c) =>
      c.last_synced_at
        ? `Last synced ${new Date(c.last_synced_at).toLocaleString()}.`
        : 'Accounts and performance data are syncing normally.',
  },
  error: {
    badge: 'Needs attention',
    variant: 'destructive',
    description: (c) => c.error ?? 'Something went wrong with this connection.',
  },
};

export function AccountsPanel({ accounts, connection }: { accounts: Account[]; connection: MetaConnection }) {
  const router = useRouter();
  const [msg, setMsg] = useState<{ text: string; kind: MsgKind } | null>(null);
  const [connecting, setConnecting] = useState(false);
  const messageListenerRef = useRef<((e: MessageEvent) => void) | null>(null);
  const closeWatcherRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const state = connState(connection);
  const copy = statusCopy[state];

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
          text: 'Your browser blocked the connect window. Allow pop-ups for this site and try again.',
          kind: 'error',
        });
        return;
      }
      setMsg({ text: 'Waiting for you to finish connecting in the popup.', kind: 'info' });

      const onMessage = async (e: MessageEvent) => {
        if (e.origin !== window.location.origin || e.data?.type !== 'fivetran:return') return;
        window.removeEventListener('message', onMessage);
        messageListenerRef.current = null;
        stopWatchingPopup();
        popup.close();
        try {
          await api.fivetranConnectFinalize(connector_id);
          setMsg({ text: 'Connected. Accounts will appear once the first sync finishes.', kind: 'success' });
          router.refresh();
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
          setMsg({ text: 'Connect window closed before finishing. Try again when ready.', kind: 'info' });
        }
      }, 500);
    } catch (e) {
      setConnecting(false);
      if (e instanceof ApiError && e.status === 403) {
        setMsg({ text: "You don't have permission to do this. Ask an admin.", kind: 'error' });
      } else if (e instanceof ApiError && e.status === 409) {
        // The connection status shown above should already have made this unreachable
        // via a normal click (the Connect button only renders in state 'none'). This is
        // a genuine race (opened in two tabs, or someone else just connected) rather
        // than a state this component needs to track -- resync from the server instead
        // of hand-maintaining a flag for it.
        setMsg({ text: 'A Meta connection already exists for this workspace. Refreshing.', kind: 'info' });
        router.refresh();
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
      setMsg({ text: 'Disconnected. Click Connect Meta to set up a new connection.', kind: 'info' });
      router.refresh();
    } catch (e) {
      setMsg({ text: `Failed to disconnect: ${e instanceof Error ? e.message : 'Something went wrong.'}`, kind: 'error' });
    } finally {
      setConnecting(false);
    }
  };

  const reconnect = async () => {
    if (!window.confirm('This replaces the existing Meta connection with a new one. Continue?')) return;
    setConnecting(true);
    setMsg(null);
    try {
      await api.fivetranDisconnect();
    } catch (e) {
      setConnecting(false);
      setMsg({
        text: `Failed to disconnect the existing connection: ${e instanceof Error ? e.message : 'Something went wrong.'}`,
        kind: 'error',
      });
      return;
    }
    await connect();
  };

  const activate = async (id: string) => {
    try {
      const { job_id } = await api.activateAccount(id);
      setMsg({
        text: job_id ? 'Sync queued. Creatives will appear as they sync.' : 'Activated. Connect Meta first to start syncing.',
        kind: 'success',
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setMsg({ text: "You don't have permission to do this. Ask an admin.", kind: 'error' });
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
        setMsg({ text: "You don't have permission to do this. Ask an admin.", kind: 'error' });
      } else {
        setMsg({ text: `Failed to sync: ${e instanceof Error ? e.message : 'Something went wrong.'}`, kind: 'error' });
      }
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Ad accounts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-3 rounded-lg border border-border p-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-2.5">
            {state === 'error' ? (
              <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
            ) : (
              <Plug className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            )}
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">Meta connection</span>
                <Badge variant={copy.variant}>{copy.badge}</Badge>
              </div>
              <p className="text-xs text-muted-foreground">{copy.description(connection)}</p>
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            {state === 'none' ? (
              <Button size="sm" onClick={connect} disabled={connecting} className="gap-1.5">
                {connecting ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Plug className="size-4" aria-hidden="true" />}
                {connecting ? 'Connecting…' : 'Connect Meta'}
              </Button>
            ) : (
              <>
                <Button
                  size="sm"
                  variant={state === 'error' ? 'default' : 'outline'}
                  onClick={reconnect}
                  disabled={connecting}
                  className="gap-1.5"
                >
                  {connecting ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="size-4" aria-hidden="true" />}
                  Reconnect
                </Button>
                <Button size="sm" variant="destructive" onClick={disconnect} disabled={connecting} className="gap-1.5">
                  <Unplug className="size-4" aria-hidden="true" />
                  Disconnect
                </Button>
              </>
            )}
          </div>
        </div>

        {msg && (
          <p role="status" className={`text-sm ${msgClass[msg.kind]}`}>
            {msg.text}
          </p>
        )}

        {accounts.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {state === 'none'
              ? 'No accounts yet. Connect Meta to pick accounts.'
              : 'Accounts will appear here once the sync finds them.'}
          </p>
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
      </CardContent>
    </Card>
  );
}
