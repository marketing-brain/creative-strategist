import { authHeaders } from './auth-headers';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const base = () => (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(await authHeaders()), ...((init.headers as Record<string, string>) ?? {}) };
  if (typeof init.body === 'string' && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${base()}${path}`, { ...init, headers, cache: 'no-store' });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail ?? msg; } catch { /* keep statusText */ }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  me: () => apiFetch<{ user_id: string; org_id: string; role: string }>('/me'),
  accounts: () => apiFetch<import('./types').Account[]>('/accounts'),
  activateAccount: (id: string) => apiFetch<{ job_id: string | null }>(`/accounts/${id}/activate`, { method: 'POST' }),
  syncStatus: () => apiFetch<import('./types').SyncStatus>('/connections/meta/sync'),
  syncNow: () => apiFetch<import('./types').SyncStatus>('/connections/meta/sync', { method: 'POST' }),
  fivetranStatus: () => apiFetch<import('./types').MetaConnection>('/connections/meta'),
  fivetranConnectStart: () => apiFetch<{ connect_card_url: string; connector_id: string }>('/connections/meta/start', { method: 'POST' }),
  fivetranConnectFinalize: (connectorId: string) => apiFetch<{ ok: boolean }>('/connections/meta/finalize', { method: 'POST', body: JSON.stringify({ connector_id: connectorId }) }),
  fivetranDisconnect: () => apiFetch<{ ok: boolean }>('/connections/meta/disconnect', { method: 'POST' }),
  creatives: (q: string) =>
    apiFetch<import('./types').Paginated<import('./types').Creative>>(`/creatives${q}`),
  creative: (id: string) => apiFetch<import('./types').Creative>(`/creatives/${id}`),
  creativeSummary: (id: string, scope: import('./types').Scope = 'footage') =>
    apiFetch<import('./types').CreativeSummary>(`/creatives/${id}/summary?scope=${scope}`),
  creativePatterns: (id: string) => apiFetch<import('./types').CreativePattern[]>(`/creatives/${id}/patterns`),
  creativeFootage: (id: string) => apiFetch<import('./types').Footage>(`/creatives/${id}/footage`),
  creativeMetrics: (id: string, since: string, until: string, scope: import('./types').Scope = 'footage') =>
    apiFetch<import('./types').MetricDay[]>(`/creatives/${id}/metrics?since=${since}&until=${until}&scope=${scope}`),
  creativeDiagnosis: (id: string, scope: import('./types').Scope = 'footage') =>
    apiFetch<import('./types').Diagnosis>(`/creatives/${id}/diagnosis?scope=${scope}`),
  creativePlayCurve: (id: string, scope: import('./types').Scope = 'footage') =>
    apiFetch<import('./types').PlayCurve | null>(`/creatives/${id}/play-curve?scope=${scope}`),
  creativeInsight: (id: string) => apiFetch<import('./types').CreativeInsight>(`/creatives/${id}/insight`),
  creativeTags: (id: string) => apiFetch<import('./types').Tag[]>(`/creatives/${id}/tags`),
  dimensions: () => apiFetch<import('./types').Dimension[]>('/tags/dimensions'),
  dimensionValues: (name: string) => apiFetch<import('./types').DimensionValue[]>(`/tags/dimensions/${encodeURIComponent(name)}/values`),
  creativeTranscript: (id: string) => apiFetch<{ transcript: string | null }>(`/creatives/${id}/transcript`),
  accountRead: (accountId: string) => apiFetch<import('./types').AccountRead>(`/insights/account-read?ad_account_id=${accountId}`),
  nextPlan: (accountId: string) => apiFetch<import('./types').NextPlan>(`/insights/next?ad_account_id=${accountId}`),
  accountReadAsk: (accountId: string, question: string) =>
    apiFetch<import('./types').AccountReadAnswer>('/insights/account-read/ask', { method: 'POST', body: JSON.stringify({ ad_account_id: accountId, question }) }),
};
