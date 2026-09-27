import { authHeaders } from './auth-headers';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

const base = () => (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(await authHeaders()), ...((init.headers as Record<string, string>) ?? {}) };
  if (typeof init.body === 'string' && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${base()}${path}`, { ...init, headers, cache: 'no-store' });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {
      /* keep statusText */
    }
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
  fivetranConnectFinalize: (connectorId: string) =>
    apiFetch<{ ok: boolean }>('/connections/meta/finalize', { method: 'POST', body: JSON.stringify({ connector_id: connectorId }) }),
  fivetranDisconnect: () => apiFetch<{ ok: boolean }>('/connections/meta/disconnect', { method: 'POST' }),
  libraryTree: (q: string) => apiFetch<import('./types').LibraryTree>(`/library/tree${q}`),
  libraryAds: (q: string) => apiFetch<import('./types').LibraryAds>(`/library/ads${q}`),
  experiments: (kind?: 'test' | 'research') =>
    apiFetch<import('./types').ExperimentSummary[]>(`/experiments${kind ? `?kind=${kind}` : ''}`),
  whatWorks: () => apiFetch<import('./types').WhatWorks>('/what-works'),
  writeCreatorScript: (cluster: string) =>
    apiFetch<import('./types').CreatorScript>('/what-works/creator-script', { method: 'POST', body: JSON.stringify({ cluster }) }),
  makeScriptVideo: (cluster: string) =>
    apiFetch<{ id: string; status: string }>('/what-works/script-video', { method: 'POST', body: JSON.stringify({ cluster }) }),
  approveScriptVideo: (id: string) =>
    apiFetch<{ id: string; status: string }>(`/what-works/script-video/${id}/approve`, { method: 'POST' }),
  finding: (id: string) => apiFetch<import('./types').FocusFinding>(`/what-works/findings/${id}`),
  experiment: (id: string) => apiFetch<import('./types').Experiment>(`/experiments/${id}`),
  experimentAdsets: (id: string) => apiFetch<{ label: string; files: number }[]>(`/experiments/${id}/adsets`),
  experimentAdsetRead: (id: string, label: string) => apiFetch<import('./types').AdSetRead>(`/experiments/${id}/adset-read?label=${encodeURIComponent(label)}`),
  experimentPatterns: (id: string) => apiFetch<import('./types').PairedPatterns>(`/experiments/${id}/patterns`),
  experimentThemes: (id: string, axis: string, minSpend = 0, window = 'first_7d') =>
    apiFetch<import('./types').ThemeView>(`/experiments/${id}/themes?axis=${axis}&min_spend=${minSpend}&window=${window}`),
  experimentClusterRead: (id: string, key: string, axis: string, minSpend = 0, window = 'last_30d') =>
    apiFetch<import('./types').AdSetRead>(`/experiments/${id}/cluster-read?key=${encodeURIComponent(key)}&axis=${axis}&min_spend=${minSpend}&window=${window}`),
  experimentClusterCompare: (id: string, axis: string, minSpend = 0, window = 'last_30d') =>
    apiFetch<import('./types').ClusterCompare>(`/experiments/${id}/cluster-compare?axis=${axis}&min_spend=${minSpend}&window=${window}`),
  experimentProfileRead: (id: string, key: string) =>
    apiFetch<import('./types').ProfileRead>(`/experiments/${id}/profile-read?key=${encodeURIComponent(key)}`),
  experimentClusterLeads: (id: string, key: string) =>
    apiFetch<import('./types').ClusterLeads>(`/experiments/${id}/cluster-leads?key=${encodeURIComponent(key)}`),
  experimentClusterInsights: (id: string, key: string) =>
    apiFetch<import('./types').ClusterInsights>(`/experiments/${id}/cluster-insights?key=${encodeURIComponent(key)}`),
  creatives: (q: string) => apiFetch<import('./types').Paginated<import('./types').Creative>>(`/creatives${q}`),
  creativeCoverage: (accountId: string, format = 'video') =>
    apiFetch<import('./types').Coverage>(`/creatives/coverage?ad_account_id=${accountId}&format=${format}`),
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
  creativeTags: (id: string) => apiFetch<import('./types').Tag[]>(`/creatives/${id}/tags`),
  dimensions: () => apiFetch<import('./types').Dimension[]>('/tags/dimensions'),
  dimensionValues: (name: string) => apiFetch<import('./types').DimensionValue[]>(`/tags/dimensions/${encodeURIComponent(name)}/values`),
  creativeTranscript: (id: string) => apiFetch<{ transcript: string | null }>(`/creatives/${id}/transcript`),
  accountRead: (accountId: string) => apiFetch<import('./types').AccountRead>(`/insights/account-read?ad_account_id=${accountId}`),
  nextPlan: (accountId: string) => apiFetch<import('./types').NextPlan>(`/insights/next?ad_account_id=${accountId}`),
  accountReadAsk: (accountId: string, question: string) =>
    apiFetch<import('./types').AccountReadAnswer>('/insights/account-read/ask', {
      method: 'POST',
      body: JSON.stringify({ ad_account_id: accountId, question }),
    }),
  briefs: (accountId: string) => apiFetch<import('./types').BriefSummary[]>(`/briefs?ad_account_id=${accountId}`),
  briefCandidates: (accountId: string) =>
    apiFetch<import('./types').BriefCandidates>(`/briefs/candidates?ad_account_id=${accountId}`),
  brief: (id: string) => apiFetch<import('./types').Brief>(`/briefs/${id}`),
  createBrief: (accountId: string, footageId: string, findingId?: string | null) =>
    apiFetch<{ id: string }>('/briefs', {
      method: 'POST',
      body: JSON.stringify({ ad_account_id: accountId, footage_id: footageId, finding_id: findingId ?? null }),
    }),
  saveBrief: (id: string, version: number, title: string, content: import('./types').BriefContent) =>
    apiFetch<{ version: number }>(`/briefs/${id}`, { method: 'PUT', body: JSON.stringify({ version, title, content }) }),
  deleteBrief: (id: string) => apiFetch<void>(`/briefs/${id}`, { method: 'DELETE' }),
  renderBriefVariant: (id: string, code: string) =>
    apiFetch<{ id: string }>(`/briefs/${id}/render`, { method: 'POST', body: JSON.stringify({ code }) }),
  uploadBriefOriginal: (id: string, file: File) =>
    apiFetch<{ duration_s: number }>(`/briefs/${id}/original`, {
      method: 'POST',
      body: file,
      headers: { 'Content-Type': file.type || 'video/mp4' },
    }),
  generations: (accountId: string) => apiFetch<import('./types').Generation[]>(`/generate?ad_account_id=${accountId}`),
  generateCandidates: (accountId: string) =>
    apiFetch<import('./types').GenerateCandidates>(`/generate/candidates?ad_account_id=${accountId}`),
  generatePlan: (footageId: string) => apiFetch<import('./types').GeneratePlan>(`/generate/plan?footage_id=${footageId}`),
  generation: (id: string) => apiFetch<import('./types').Generation>(`/generate/${id}`),
  createGeneration: (accountId: string, footageId: string, prompt: string, resolution: string) =>
    apiFetch<{ id: string }>('/generate', {
      method: 'POST',
      body: JSON.stringify({ ad_account_id: accountId, footage_id: footageId, prompt, resolution }),
    }),
  deleteGeneration: (id: string) => apiFetch<void>(`/generate/${id}`, { method: 'DELETE' }),
  makeOverview: (accountId: string) => apiFetch<import('./types').MakeOverview>(`/make?ad_account_id=${accountId}`),
  makeFromCreative: (creativeId: string) => apiFetch<{ footage_id: string }>(`/make/from-creative/${creativeId}`),
  makeVideo: (footageId: string, findingId?: string) =>
    apiFetch<import('./types').MakeVideo>(`/make/${footageId}${findingId ? `?finding=${encodeURIComponent(findingId)}` : ''}`),
  makeFindingEdit: (footageId: string, findingId: string) =>
    apiFetch<{ brief_id: string; code: string; cut: string[] }>(`/make/${footageId}/finding-edit`, { method: 'POST', body: JSON.stringify({ finding_id: findingId }) }),
  makeDecide: (footageId: string, ref: string, decision: 'in' | 'skipped' | 'none') =>
    apiFetch<{ ok: boolean }>(`/make/${footageId}/decide`, { method: 'POST', body: JSON.stringify({ ref, decision }) }),
  makeLaunch: (footageId: string) => apiFetch<{ experiment_id: string }>(`/make/${footageId}/launch`, { method: 'POST' }),
};
