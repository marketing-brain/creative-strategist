'use client';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { AlertTriangle, ArrowRight, CheckCircle2, CircleDashed, Loader2, XCircle } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ApiError, api } from '@/lib/api';
import type { Account, SyncStatus } from '@/lib/types';
import { backlogTotal, backlogWords, fivetranStale, fmtDay, fmtWhen, gap, summarize, syncBusy, type Tone } from '@/lib/sync-view';

// Every button on this page keeps its label, variant and place whatever the state: an action
// that cannot run right now is disabled with the reason beside it, never swapped for another.
// State lives in the status line at the top, not in the buttons. Anything that spends money or
// cuts the connection asks first, in a dialog that says exactly what will happen.

const POLL_BUSY_MS = 3_000;

const toneStyle: Record<Tone, { icon: ReactNode; text: string }> = {
  ok: { icon: <CheckCircle2 className="size-5 text-success" aria-hidden="true" />, text: 'text-foreground' },
  busy: { icon: <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden="true" />, text: 'text-foreground' },
  warn: { icon: <AlertTriangle className="size-5 text-chart-2" aria-hidden="true" />, text: 'text-foreground' },
  error: { icon: <XCircle className="size-5 text-destructive" aria-hidden="true" />, text: 'text-destructive' },
  idle: { icon: <CircleDashed className="size-5 text-muted-foreground" aria-hidden="true" />, text: 'text-foreground' },
};

const errorText = (e: unknown, what: string) =>
  e instanceof ApiError && e.status === 403
    ? 'Only admins can do this.'
    : `Could not ${what}: ${e instanceof Error ? e.message : 'something went wrong.'}`;

function Confirm({
  open,
  onOpenChange,
  title,
  children,
  action,
  destructive,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  children: ReactNode;
  action: string;
  destructive?: boolean;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription render={<div className="space-y-2" />}>{children}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
          <Button
            variant={destructive ? 'destructive' : 'default'}
            onClick={() => {
              onOpenChange(false);
              onConfirm();
            }}
          >
            {action}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Notice({ children, tone = 'muted' }: { children: ReactNode; tone?: 'muted' | 'error' }) {
  return (
    <p role="status" className={cn('text-sm', tone === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
      {children}
    </p>
  );
}

// ---------- Sync ----------

function Freshness({ s }: { s: SyncStatus }) {
  const src = s.source;
  const app = s.app;
  if (!src || !app) return null;
  const g = gap(s);
  const rows: { label: string; source: string; app: string; behind: boolean }[] = [
    { label: 'Ads', source: src.ads.toLocaleString('en-US'), app: app.ads.toLocaleString('en-US'), behind: g.ads > 0 },
    {
      label: 'Results through',
      source: src.has_reports ? fmtDay(src.metrics_through) : 'None',
      app: fmtDay(app.metrics_through),
      behind: g.days > 0,
    },
    { label: 'Last updated', source: fmtWhen(src.delivered_at), app: fmtWhen(s.connection?.last_synced_at), behind: false },
  ];
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] bg-muted/50 px-3 py-2 text-sm font-medium">
        <span aria-hidden="true" />
        <span className="flex items-center gap-1.5">
          Fivetran’s copy of Meta
        </span>
        <span className="flex items-center gap-1.5">
          <ArrowRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
          In this app
        </span>
      </div>
      <dl className="divide-y divide-border">
        {rows.map((r) => (
          <div key={r.label} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] px-3 py-2 text-sm">
            <dt className="text-muted-foreground">{r.label}</dt>
            <dd className="tabular-nums">{r.source}</dd>
            <dd className={cn('tabular-nums', r.behind && 'font-semibold text-chart-2')}>{r.app}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function SyncConfirmBody({ s }: { s: SyncStatus }) {
  const g = gap(s);
  const brings =
    g.ads > 0 || g.days > 0
      ? `This brings in ${[g.ads > 0 ? `${g.ads.toLocaleString('en-US')} new ads` : '', g.days > 0 ? `results for ${fmtDay(g.from)} – ${fmtDay(g.to)}` : ''].filter(Boolean).join(' and ')}.`
      : 'Fivetran has nothing newer than the app right now, so this only checks again.';
  return (
    <>
      <p>{brings}</p>
      {g.ads > 0 && (
        <p>
          Each new ad’s file is then downloaded, tagged and written up by Gemini. Videos that aren’t in the ad library are fetched through Apify.{' '}
          <span className="font-medium text-foreground">Tagging, write-ups and Apify are paid.</span>
        </p>
      )}
    </>
  );
}

function SyncCard({ s, isAdmin, onSync, busy }: { s: SyncStatus; isAdmin: boolean; onSync: () => void; busy: boolean }) {
  const [confirm, setConfirm] = useState(false);
  const summary = summarize(s);
  const style = toneStyle[summary.tone];
  const ready = s.connection?.status === 'ready';
  const running = syncBusy(s);
  const disabledWhy = !s.connection
    ? 'Connect Meta first.'
    : !ready
      ? 'Available once the connection is ready.'
      : running
        ? 'A sync is already in progress.'
        : !isAdmin
          ? 'Only admins can sync.'
          : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Meta data</CardTitle>
        <CardDescription>Fivetran copies your Meta ad account on its own schedule. A sync moves that copy into the app.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 gap-3" aria-live="polite">
            <span className="mt-0.5 shrink-0">{style.icon}</span>
            <div className="min-w-0">
              <p className={cn('text-base font-semibold', style.text)}>{summary.title}</p>
              <p className="text-sm text-muted-foreground">{summary.detail}</p>
            </div>
          </div>
          <div className="flex shrink-0 flex-col items-start gap-1 sm:items-end">
            <Button onClick={() => setConfirm(true)} disabled={!!disabledWhy || busy} aria-busy={running || busy}>
              Sync now
            </Button>
            {disabledWhy && <span className="text-xs text-muted-foreground">{disabledWhy}</span>}
          </div>
        </div>

        <Freshness s={s} />

        {s.source && !s.source.has_reports && (
          <div className="flex gap-2.5 rounded-lg border border-chart-2/40 bg-chart-2/5 p-3 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-chart-2" aria-hidden="true" />
            <p>No performance reports have arrived from Fivetran, so there is no spend or results to sync. Check the connector’s reports in Fivetran.</p>
          </div>
        )}
        {fivetranStale(s) && (
          <div className="flex gap-2.5 rounded-lg border border-chart-2/40 bg-chart-2/5 p-3 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-chart-2" aria-hidden="true" />
            <p>
              Fivetran last copied anything from Meta {fmtWhen(s.source?.delivered_at)}. Syncing here won’t bring anything newer; check that the connector is running in
              Fivetran.
            </p>
          </div>
        )}
        {running && backlogTotal(s) > 0 && <Notice>Queued behind it: {backlogWords(s)}.</Notice>}
        {!!s.held && (
          <Notice>
            {s.held.toLocaleString('en-US')} paid video lookups are on hold. They don’t run unless someone releases them.
          </Notice>
        )}

        {s.connection && s.auto_sync && (
          <p className="border-t border-border pt-3 text-sm text-muted-foreground">
            {s.auto_sync.enabled
              ? `Automatic sync is on: once a day, next ${fmtWhen(s.auto_sync.next_run_at).replace('today, ', 'today at ')}.`
              : 'Automatic sync is off. The app only syncs when someone presses Sync now.'}
            {s.last_sync?.finished_at && s.last_sync.status === 'done' ? ` Last sync finished ${fmtWhen(s.last_sync.finished_at)}.` : ''}
          </p>
        )}
      </CardContent>

      <Confirm open={confirm} onOpenChange={setConfirm} title="Sync from Meta?" action="Sync now" onConfirm={onSync}>
        <SyncConfirmBody s={s} />
      </Confirm>
    </Card>
  );
}

// ---------- Accounts ----------

function AccountsCard({ accounts, isAdmin, connected }: { accounts: Account[]; isAdmin: boolean; connected: boolean }) {
  const router = useRouter();
  const [pending, setPending] = useState<Account | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);

  const include = async (a: Account) => {
    setWorking(a.id);
    setMsg(null);
    try {
      await api.activateAccount(a.id);
      setMsg({ text: `${a.name} is included. Its first sync has started.` });
      router.refresh();
    } catch (e) {
      setMsg({ text: errorText(e, 'include this account'), error: true });
    } finally {
      setWorking(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Ad accounts</CardTitle>
        <CardDescription>Only included accounts are synced. Others stay in Fivetran and never reach the app.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {accounts.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
            {connected ? 'Accounts appear here once Fivetran finds them.' : 'Connect Meta to see your ad accounts.'}
          </p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {accounts.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{a.name && a.name !== a.platform_account_id ? a.name : 'Meta ad account'}</p>
                  <p className="text-sm text-muted-foreground tabular-nums">
                    {a.platform_account_id} · {a.currency}
                  </p>
                </div>
                {a.status === 'active' ? (
                  <span className="flex shrink-0 items-center gap-1.5 text-sm text-success">
                    <CheckCircle2 className="size-4" aria-hidden="true" />
                    Included
                  </span>
                ) : a.status === 'disconnected' ? (
                  <span className="shrink-0 text-sm text-muted-foreground">Disconnected</span>
                ) : (
                  <Button variant="outline" size="sm" disabled={!isAdmin || working !== null} aria-busy={working === a.id} onClick={() => setPending(a)}>
                    Include in sync
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {msg && <Notice tone={msg.error ? 'error' : 'muted'}>{msg.text}</Notice>}
      </CardContent>

      <Confirm
        open={pending !== null}
        onOpenChange={(o) => !o && setPending(null)}
        title={`Include ${pending?.name ?? 'this account'}?`}
        action="Include and sync"
        onConfirm={() => pending && include(pending)}
      >
        <p>Its ads and results come into the app, starting with a sync now.</p>
        <p>
          Each ad’s file is then downloaded, tagged and written up by Gemini, and some videos are fetched through Apify.{' '}
          <span className="font-medium text-foreground">Those steps are paid.</span>
        </p>
      </Confirm>
    </Card>
  );
}

// ---------- Connection ----------

function useFivetranConnect(onDone: (text: string, error?: boolean) => void) {
  const router = useRouter();
  const [connecting, setConnecting] = useState(false);
  const listenerRef = useRef<((e: MessageEvent) => void) | null>(null);
  const watcherRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = () => {
    if (watcherRef.current) clearInterval(watcherRef.current);
    watcherRef.current = null;
    if (listenerRef.current) window.removeEventListener('message', listenerRef.current);
    listenerRef.current = null;
  };
  useEffect(() => stop, []);

  const connect = async (retryingAfterDisconnect = false): Promise<void> => {
    // A connect attempt that never finished (popup closed, no return message) must not leave
    // its listener live, or it double-finalizes on the next success with a stale connector id.
    stop();
    setConnecting(true);
    try {
      const { connect_card_url, connector_id } = await api.fivetranConnectStart();
      const width = 520;
      const height = 720;
      const left = Math.round(window.screenX + Math.max(0, (window.outerWidth - width) / 2));
      const top = Math.round(window.screenY + Math.max(0, (window.outerHeight - height) / 2));
      const popup = window.open(connect_card_url, 'fivetran-connect', `width=${width},height=${height},left=${left},top=${top},popup=1`);
      if (!popup) {
        setConnecting(false);
        onDone('Your browser blocked the connect window. Allow pop-ups for this site and try again.', true);
        return;
      }
      onDone('Finish connecting in the Fivetran window.');
      const onMessage = async (e: MessageEvent) => {
        if (e.origin !== window.location.origin || e.data?.type !== 'fivetran:return') return;
        stop();
        popup.close();
        try {
          await api.fivetranConnectFinalize(connector_id);
          onDone('Connected. Fivetran is running its first copy; accounts appear when it finishes.');
          router.refresh();
        } catch (err) {
          onDone(errorText(err, 'finish connecting'), true);
        } finally {
          setConnecting(false);
        }
      };
      listenerRef.current = onMessage;
      window.addEventListener('message', onMessage);
      // Closing the popup without finishing sends no message; without this the page waits forever.
      watcherRef.current = setInterval(() => {
        if (popup.closed) {
          stop();
          setConnecting(false);
          onDone('The Fivetran window closed before connecting finished.');
        }
      }, 500);
    } catch (e) {
      // 409: a live connector still exists for this org (disconnect keeps the row as 'error').
      // Clear it and try once more rather than dead-ending here.
      if (e instanceof ApiError && e.status === 409 && !retryingAfterDisconnect) {
        try {
          await api.fivetranDisconnect();
        } catch (err) {
          setConnecting(false);
          onDone(errorText(err, 'clear the old connection'), true);
          return;
        }
        return connect(true);
      }
      setConnecting(false);
      onDone(errorText(e, 'connect'), true);
      if (retryingAfterDisconnect) router.refresh();
    }
  };

  return { connect, connecting };
}

function ConnectionCard({ s, isAdmin }: { s: SyncStatus; isAdmin: boolean }) {
  const router = useRouter();
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [dialog, setDialog] = useState<'reconnect' | 'disconnect' | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const { connect, connecting } = useFivetranConnect((text, error) => setMsg({ text, error }));
  const c = s.connection;
  const busy = connecting || disconnecting;

  const disconnect = async () => {
    setDisconnecting(true);
    setMsg(null);
    try {
      await api.fivetranDisconnect();
      setMsg({ text: 'Disconnected. The data already in the app stays.' });
      router.refresh();
    } catch (e) {
      setMsg({ text: errorText(e, 'disconnect'), error: true });
    } finally {
      setDisconnecting(false);
    }
  };

  const status = !c ? 'Not connected' : c.status === 'error' ? 'Needs attention' : c.status === 'pending' ? 'Setting up' : 'Connected through Fivetran';

  return (
    <Card>
      <CardHeader>
        <CardTitle>Meta connection</CardTitle>
        <CardDescription>The link Fivetran uses to read your Meta ad account. Read-only: nothing is ever changed in Meta.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className={cn('text-sm font-medium', c?.status === 'error' && 'text-destructive')}>{status}</p>
          <div className="flex shrink-0 gap-2">
            {c ? (
              <>
                <Button variant="outline" disabled={!isAdmin || busy} aria-busy={connecting} onClick={() => setDialog('reconnect')}>
                  Reconnect
                </Button>
                <Button variant="destructive" disabled={!isAdmin || busy} aria-busy={disconnecting} onClick={() => setDialog('disconnect')}>
                  Disconnect
                </Button>
              </>
            ) : (
              <Button disabled={!isAdmin || busy} aria-busy={connecting} onClick={() => connect()}>
                Connect Meta
              </Button>
            )}
          </div>
        </div>
        {busy && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            {disconnecting ? 'Disconnecting…' : 'Waiting for Fivetran…'}
          </p>
        )}
        {!busy && msg && <Notice tone={msg.error ? 'error' : 'muted'}>{msg.text}</Notice>}
        {!isAdmin && <Notice>Only admins can change the connection.</Notice>}
      </CardContent>

      <Confirm
        open={dialog === 'reconnect'}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Replace the Meta connection?"
        action="Reconnect"
        onConfirm={() => connect()}
      >
        <p>This removes the current Fivetran connector and opens Fivetran to authorize Meta again.</p>
        <p>Data already in the app stays. Syncing pauses until Fivetran finishes its first copy.</p>
      </Confirm>
      <Confirm
        open={dialog === 'disconnect'}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Disconnect Meta?"
        action="Disconnect"
        destructive
        onConfirm={disconnect}
      >
        <p>Fivetran stops copying your Meta account and nothing new reaches the app.</p>
        <p>Data already in the app stays. You can connect again at any time.</p>
      </Confirm>
    </Card>
  );
}

// ---------- Page body ----------

export function MetaSyncSettings({ initial, accounts, role }: { initial: SyncStatus; accounts: Account[]; role: string }) {
  const router = useRouter();
  const [s, setS] = useState(initial);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isAdmin = role === 'admin';
  const wasBusy = useRef(syncBusy(initial) || backlogTotal(initial) > 0);

  // A refresh re-renders the server page with a new `initial`; take it.
  useEffect(() => setS(initial), [initial]);

  const refresh = useCallback(async () => {
    try {
      setS(await api.syncStatus());
    } catch {
      /* keep the last good read; the next tick tries again */
    }
  }, []);

  const busy = syncBusy(s) || backlogTotal(s) > 0 || s.connection?.status === 'pending';
  useEffect(() => {
    if (!busy) {
      // Work just finished: accounts and every other page's numbers have moved.
      if (wasBusy.current) router.refresh();
      wasBusy.current = false;
      return;
    }
    wasBusy.current = true;
    const t = setInterval(refresh, POLL_BUSY_MS);
    return () => clearInterval(t);
  }, [busy, refresh, router]);

  const syncNow = async () => {
    setStarting(true);
    setError(null);
    try {
      setS(await api.syncNow());
    } catch (e) {
      setError(errorText(e, 'start the sync'));
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="space-y-6">
      <SyncCard s={s} isAdmin={isAdmin} onSync={syncNow} busy={starting} />
      {error && <Notice tone="error">{error}</Notice>}
      <AccountsCard accounts={accounts} isAdmin={isAdmin} connected={!!s.connection} />
      <ConnectionCard s={s} isAdmin={isAdmin} />
    </div>
  );
}
