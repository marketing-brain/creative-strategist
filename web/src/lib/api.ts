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
  syncAccount: (id: string) => apiFetch<{ job_id: string | null }>(`/accounts/${id}/sync`, { method: 'POST' }),
  fivetranConnectStart: () => apiFetch<{ connect_card_url: string; connector_id: string }>('/connections/meta/start', { method: 'POST' }),
  fivetranConnectFinalize: (connectorId: string) => apiFetch<{ ok: boolean }>('/connections/meta/finalize', { method: 'POST', body: JSON.stringify({ connector_id: connectorId }) }),
  brandKit: () => apiFetch<import('./types').BrandKit>('/brand-kit'),
  saveBrandKit: (b: Omit<import('./types').BrandKit, 'id' | 'version' | 'logo_url'>) =>
    apiFetch<import('./types').BrandKit>('/brand-kit', { method: 'POST', body: JSON.stringify(b) }),
  creatives: (q: string) => apiFetch<import('./types').Creative[]>(`/creatives${q}`),
  creative: (id: string) => apiFetch<import('./types').Creative>(`/creatives/${id}`),
  creativeMetrics: (id: string, since: string, until: string) =>
    apiFetch<import('./types').MetricDay[]>(`/creatives/${id}/metrics?since=${since}&until=${until}`),
  creativeTags: (id: string) => apiFetch<import('./types').Tag[]>(`/creatives/${id}/tags`),
  dimensions: () => apiFetch<import('./types').Dimension[]>('/tags/dimensions'),
  insights: (kind: string) => apiFetch<import('./types').Insight[]>(`/insights?kind=${kind}`),
  fatigue: () => apiFetch<import('./types').Insight[]>('/fatigue'),
  briefs: () => apiFetch<import('./types').Brief[]>('/briefs'),
  conversations: () => apiFetch<import('./types').Conversation[]>('/copilot/conversations'),
  newConversation: (title: string) => apiFetch<{ id: string }>('/copilot/conversations', { method: 'POST', body: JSON.stringify({ title }) }),
  messages: (id: string) => apiFetch<import('./types').Message[]>(`/copilot/conversations/${id}`),
  send: (id: string, content: string) => apiFetch<{ reply: string }>(`/copilot/conversations/${id}/messages`, { method: 'POST', body: JSON.stringify({ content }) }),
  generations: () => apiFetch<import('./types').Generation[]>('/generations'),
  generation: (id: string) => apiFetch<import('./types').Generation>(`/generations/${id}`),
  createGeneration: (b: object) => apiFetch<{ id: string }>('/generations', { method: 'POST', body: JSON.stringify(b) }),
  reviewQueue: (state: string) => apiFetch<import('./types').GeneratedAsset[]>(`/review-queue?state=${state}`),
  approve: (id: string) => apiFetch<{ ok: boolean }>(`/generated-assets/${id}/approve`, { method: 'POST' }),
  reject: (id: string) => apiFetch<{ ok: boolean }>(`/generated-assets/${id}/reject`, { method: 'POST' }),
};
