// What the settings page says about the Meta sync, derived from one /connections/meta/sync
// read. Pure, so the headline can never disagree with the numbers under it.
import type { SyncStatus } from './types';

export type Tone = 'ok' | 'busy' | 'warn' | 'error' | 'idle';
export type Summary = { tone: Tone; title: string; detail: string };

const DAY_MS = 86_400_000;
// Fivetran runs several times a day; two quiet days means it has stopped, not that Meta is quiet.
const FIVETRAN_STALE_MS = 2 * DAY_MS;

export const fmtDay = (iso: string | null | undefined) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) : '—';

export function fmtWhen(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '—';
  const t = new Date(iso);
  const mins = Math.round((now - t.getTime()) / 60_000);
  if (mins >= 0 && mins < 1) return 'just now';
  if (mins >= 0 && mins < 60) return `${mins} min ago`;
  const time = t.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  const today = new Date(now).toDateString() === t.toDateString();
  if (today) return `today, ${time}`;
  return `${t.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}, ${time}`;
}

const dayDiff = (later: string | null | undefined, earlier: string | null | undefined) =>
  later && earlier ? Math.max(0, Math.round((Date.parse(later.slice(0, 10)) - Date.parse(earlier.slice(0, 10))) / DAY_MS)) : 0;

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;

export type Gap = { ads: number; days: number; from: string | null; to: string | null };

// What a sync would bring in: ads Fivetran has that the app does not, and the days of results after
// the app's last day. A first sync of results (app has none) counts from Fivetran's own range.
export function gap(s: SyncStatus): Gap {
  const src = s.source;
  const app = s.app;
  if (!src || !app) return { ads: 0, days: 0, from: null, to: null };
  const days = app.metrics_through ? dayDiff(src.metrics_through, app.metrics_through) : 0;
  const from = app.metrics_through && days > 0 ? new Date(Date.parse(app.metrics_through) + DAY_MS).toISOString().slice(0, 10) : null;
  return { ads: Math.max(0, src.ads - app.ads), days, from, to: days > 0 ? src.metrics_through : null };
}

export function syncBusy(s: SyncStatus): boolean {
  const st = s.last_sync?.status;
  return st === 'pending' || st === 'running';
}

export function backlogTotal(s: SyncStatus): number {
  const b = s.backlog;
  return b ? b.files + b.tagging + b.insights : 0;
}

export function backlogWords(s: SyncStatus): string {
  const b = s.backlog;
  if (!b) return '';
  return [
    b.files ? `${plural(b.files, 'file')} to fetch` : '',
    b.tagging ? `${plural(b.tagging, 'ad')} to tag` : '',
    b.insights ? `${plural(b.insights, 'write-up')} to go` : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

export function fivetranStale(s: SyncStatus, now = Date.now()): boolean {
  const at = s.source?.delivered_at;
  return !!at && now - Date.parse(at) > FIVETRAN_STALE_MS;
}

export function summarize(s: SyncStatus): Summary {
  const c = s.connection;
  if (!c) return { tone: 'idle', title: 'Not connected', detail: 'Connect Meta to bring in your ads and their results.' };
  if (c.status === 'error') return { tone: 'error', title: 'Connection needs attention', detail: c.error ?? 'The Meta connection stopped working. Reconnect to resume.' };

  const job = s.last_sync;
  if (c.status === 'pending' || (job?.kind === 'first_sync' && syncBusy(s)))
    return { tone: 'busy', title: 'Setting up', detail: 'Fivetran is copying your Meta account for the first time. This can take a while.' };
  if (job && syncBusy(s)) {
    if (job.status === 'running') return { tone: 'busy', title: 'Syncing', detail: 'Bringing in ads and results from Fivetran’s copy of Meta.' };
    return job.retrying
      ? { tone: 'busy', title: 'Retrying sync', detail: job.error ? `The last attempt failed: ${job.error}` : 'The last attempt failed and will run again shortly.' }
      : { tone: 'busy', title: 'Sync queued', detail: 'It starts within a minute.' };
  }
  if (job?.status === 'dead') return { tone: 'error', title: 'Last sync failed', detail: job.error ?? 'The sync stopped after several attempts.' };
  if (backlogTotal(s) > 0) return { tone: 'busy', title: 'Processing new ads', detail: backlogWords(s) };

  const g = gap(s);
  if (g.ads > 0 || g.days > 0) {
    const title = g.days > 0 ? `${plural(g.days, 'day')} behind Meta` : `${plural(g.ads, 'new ad')} waiting`;
    const parts = [g.ads > 0 ? plural(g.ads, 'new ad') : '', g.days > 0 ? `results for ${fmtDay(g.from)} – ${fmtDay(g.to)}` : ''].filter(Boolean);
    return { tone: 'warn', title, detail: `Fivetran has ${parts.join(' and ')} that the app doesn’t yet. Sync to bring them in.` };
  }
  if (fivetranStale(s)) return { tone: 'warn', title: 'Fivetran has stopped delivering', detail: 'The app has everything Fivetran has, but Fivetran hasn’t copied anything new from Meta recently.' };
  return { tone: 'ok', title: 'Up to date', detail: 'Everything Fivetran has copied from Meta is in the app.' };
}
