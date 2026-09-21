import { describe, expect, it } from 'vitest';
import { gap, summarize } from '@/lib/sync-view';
import type { SyncStatus } from '@/lib/types';

const base: SyncStatus = {
  connection: { status: 'ready', error: null, last_synced_at: '2026-09-14T17:06:46Z' },
  source: { ads: 1358, metrics_through: '2026-09-21', has_reports: true, delivered_at: new Date().toISOString() },
  app: { ads: 1197, metrics_through: '2026-09-14' },
  last_sync: { id: 'j', kind: 'sync', status: 'done', retrying: false, error: null, queued_at: '', started_at: null, finished_at: '2026-09-14T17:06:46Z' },
  auto_sync: { enabled: false, next_run_at: null },
  backlog: { files: 0, tagging: 0, insights: 0 },
};

describe('sync view', () => {
  it('counts what a sync would bring in', () => {
    expect(gap(base)).toEqual({ ads: 161, days: 7, from: '2026-09-15', to: '2026-09-21' });
    expect(summarize(base)).toMatchObject({ tone: 'warn', title: '7 days behind Meta' });
  });

  it('a running sync outranks the gap', () => {
    const s = { ...base, last_sync: { ...base.last_sync!, status: 'running' as const } };
    expect(summarize(s)).toMatchObject({ tone: 'busy', title: 'Syncing' });
  });

  it('a failed sync is reported with its error', () => {
    const s = { ...base, last_sync: { ...base.last_sync!, status: 'dead' as const, error: 'boom' } };
    expect(summarize(s)).toEqual({ tone: 'error', title: 'Last sync failed', detail: 'boom' });
  });

  it('caught up is up to date', () => {
    const s = { ...base, app: { ads: 1358, metrics_through: '2026-09-21' } };
    expect(summarize(s)).toMatchObject({ tone: 'ok', title: 'Up to date' });
  });

  it('a Fivetran that stopped delivering is named even when the app has caught up', () => {
    const s = { ...base, app: { ads: 1358, metrics_through: '2026-09-21' }, source: { ...base.source!, delivered_at: '2026-09-13T01:37:04Z' } };
    expect(summarize(s).title).toBe('Fivetran has stopped delivering');
  });

  it('no connection', () => {
    expect(summarize({ connection: null }).title).toBe('Not connected');
  });
});
