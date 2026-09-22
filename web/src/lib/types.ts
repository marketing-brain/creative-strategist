export type Paginated<T> = { items: T[]; total: number; total_all?: number; total_active?: number; window?: { start: string; end: string } | null };
export type Account = { id: string; platform: string; platform_account_id: string; name: string; currency: string; status: 'active' | 'paused' | 'disconnected'; sync_cursor: Record<string, unknown>; connection_status: 'pending' | 'syncing' | 'ready' | 'error' | null; connection_error: string | null };
export type MetaConnection = { connected: boolean; status: 'pending' | 'syncing' | 'ready' | 'error' | null; error: string | null; last_synced_at: string | null };
// Both hops between Meta and the app: Fivetran's copy of Meta (source) and the app's own tables.
export type SyncJob = { id: string; kind: 'sync' | 'first_sync'; status: 'pending' | 'running' | 'done' | 'dead'; retrying: boolean; error: string | null; queued_at: string; started_at: string | null; finished_at: string | null };
export type SyncStatus = {
  connection: { status: 'pending' | 'syncing' | 'ready' | 'error'; error: string | null; last_synced_at: string | null } | null;
  source?: { ads: number; metrics_through: string | null; has_reports: boolean; delivered_at: string | null } | null;
  app?: { ads: number; metrics_through: string | null };
  last_sync?: SyncJob | null;
  auto_sync?: { enabled: boolean; next_run_at: string | null };
  backlog?: { files: number; tagging: number };
};
export type Creative = { id: string; ad_account_id: string; platform_creative_id: string; format: 'image' | 'video' | 'carousel' | 'unresolved'; headline: string | null; primary_text: string | null; cta_type: string | null; link_url: string | null; primary_asset_url: string | null; primary_asset_mime?: string | null; first_seen_at: string; distance?: number; spend_30d: number; ctr_30d?: number | null; creative_count?: number; ad_count?: number; copy_variants?: number; ad_name?: string | null; poster_url?: string | null; totals?: GridRates; baseline?: GridRates };
// A grid card's 30-day numbers, and the same numbers pooled over the rest of its account and format.
export type GridRates = { spend: number; impressions: number; link_clicks: number; purchases: number; hook_rate: number | null; link_ctr: number | null; cpa: number | null; roas: number | null };
// 30-day totals for one ad, one copy variant, or a whole footage group.
export type Totals = { spend: number; impressions: number; clicks: number; purchases: number; ctr: number | null; cpa: number | null; roas: number | null };
export type FootageAd = Totals & { id: string; creative_id: string; platform_ad_id: string; name: string | null; status: string | null; campaign_id: string | null; campaign_name: string | null; adset_id: string | null };
export type CopyVariant = Totals & { headline: string | null; primary_text: string | null; creative_ids: string[] };
// Every creative running the same file as the one asked for, itself included.
export type Footage = { creative_ids: string[]; ads: FootageAd[]; copy_variants: CopyVariant[] };
export type Scope = 'footage' | 'creative';
// Headline numbers for the diagnosis window, and the same numbers pooled over every other
// creative of the format in the account.
export type SummaryRates = { spend: number; impressions: number; link_clicks: number; purchases: number; cpm: number | null; hook_rate: number | null; link_ctr: number | null; click_to_purchase: number | null; cpa: number | null; aov: number | null; roas: number | null };
export type CreativeSummary = { window_start: string; window_end: string; totals: SummaryRates; baseline: SummaryRates; baseline_creatives: number };
// A graded account-wide main effect whose cell includes this creative's file. Evidence about
// the account, shown beside the creative; it never measures the attribute within the file.
export type CreativePattern = { has: boolean; idea: number; dimension: string; value: string; metric: 'hook_rate' | 'link_ctr' | 'thruplay_rate' | 'cpa' | 'purchases_per_1k'; grade: 'strong' | 'suggestive'; change: number; n_cell: number; n_complement: number; cell_value: number | null; comp_value: number | null; bin_edges: [number, number] | null; confounds: string[]; prescribable?: boolean; window_start?: string; window_end?: string };
export type MetricDay = { day: string; spend: number; impressions: number; clicks: number; link_clicks: number; video_3s_views: number; conversions: number; conversion_value: number; ctr: number | null; cpa: number | null; roas: number | null };
export type TagKind = 'numeric' | 'single' | 'multi' | 'ordered' | 'open' | 'text' | 'series';
export type Tag = { dimension: string; kind: TagKind; value: string; raw_value: string | null; confidence: number; evidence: string; t_start_s: number | null; ordinal: number | null };
export type Dimension = { name: string; group: string; kind: TagKind; applies_to: string[]; values: string[] | null; description: string };
export type DimensionValue = { value: string; count: number };
export type DiagnosisStatus = 'strong' | 'on_par' | 'weak' | 'not_applicable' | 'not_enough_data' | 'too_young' | 'not_measured';
export type DiagnosisMetric = { key: string; label: string; value: number | null; baseline: number | null; lift: number | null; note: string };
export type DiagnosisCurvePoint = { p: number; share: number | null; baseline_share: number | null };
// Meta's per-second play curve (0–14s one point per second, then wider buckets to 60s+).
export type PlayCurvePoint = { start_s: number; end_s: number | null; share: number | null; baseline_share: number | null };
export type PlayCurve = { points: PlayCurvePoint[]; plays: number; baseline_plays: number; baseline_label: string | null; duration_s: number | null };
export type DiagnosisCurve = { points: DiagnosisCurvePoint[]; drop_segment: [number, number] | null; avg_watch_s: number | null; duration_s: number | null; baseline_label: string | null };
export type DiagnosisStep = { key: string; label: string; rate: number | null; baseline_rate: number | null; lift: number | null; numerator: number; denominator: number; readable: boolean };
export type DiagnosisAudienceSlice = { label: string; age: string; gender: string; share: number; baseline_share: number };
export type DiagnosisTier = {
  tier: number;
  key: 'attention' | 'retention' | 'conversion' | 'fatigue' | 'context';
  name: string;
  question: string;
  status: DiagnosisStatus;
  decision: string | null;
  reason: string | null;
  metrics: DiagnosisMetric[];
  curve: DiagnosisCurve | null;
  steps: DiagnosisStep[] | null;
  audience: DiagnosisAudienceSlice[] | null;
  comparison: string | null;
};
export type Diagnosis = {
  window_start: string;
  window_end: string;
  format: 'image' | 'video' | 'carousel';
  baseline_creatives: number;
  broken_tier: number | null;
  verdict: { kind: 'broken' | 'healthy' | 'too_early'; headline: string; detail: string; converting?: boolean };
  tiers: DiagnosisTier[];
};
export type AccountReadTest = { dimension: string; value: string; group: string | null; metric: string; kind: 'per_unit' | 'pooled'; n_cell: number; n_complement: number; cell_value: number; comp_value: number; change: number; p: number; grade: string; confounds: string[]; cell_shas: string[]; bin_edges: [number, number] | null; cell_purchases: number | null; pid?: string };
export type AccountReadFinding = { title: string; layer: string; paragraph: string; mechanism: string; exemplars: string[]; exception: string; why_non_obvious: string; pattern_ids: string[]; confidence: string };
export type AccountReadText = { overview: string; findings: AccountReadFinding[]; entangled: string; no_evidence: string; next_tests: string[]; data_needed: string[] };
export type AccountReadUnit = { sha256: string; headline: string; spend: number; creative_ids: string[]; rep_creative_id: string | null; duration_s: number | null; impressions: number; purchases: number; video_3s_views?: number | null; link_clicks?: number | null; poster_url?: string | null };
export type AccountRead = {
  status: 'none' | 'done' | 'insufficient' | 'rejected' | 'failed'; n_units?: number; created_at?: string; window_start?: string | null; window_end?: string | null;
  summary?: Record<string, unknown>; tests?: AccountReadTest[]; sibling_tests?: AccountReadTest[]; overlaps?: unknown[]; stratified?: unknown[]; retention_by_reveal?: Record<string, unknown>;
  units?: AccountReadUnit[]; read?: AccountReadText | null; error?: string | null; newer_failed_error?: string | null;
};
export type NextDecline = { metric: 'cpa' | 'ctr'; baseline: number; observed: number; ratio: number; capped: boolean };
export type NextStake = { amount: number; share: number; window: 'window' | '30d'; basis: string };
export type NextAllocationRow = {
  key: string; kind: 'fix' | 'replace' | 'scale'; creative_id: string; headline: string;
  rep_creative_id: string | null; stake: NextStake; verdict: string; detail: string;
  broken_layer: string | null; decline: NextDecline | null; poster_url?: string | null;
};
export type NextEvidence = { pattern_id: string; label: string; metric: string; metric_name: string; change: number; grade: string; n_cell: number; n_complement: number; cell_value: number; comp_value: number };
export type NextRef = { sha256: string; headline: string; rep_creative_id: string | null; spend: number; shows: string; poster_url?: string | null };
export type NextShape = { duration_s: number | null; duration_range: [number, number] | null; first_product_s: number | null; captions: string | null; n_carriers: number };
export type NextWatch = { metric: string; metric_name: string; account_value: number; account_label: string; direction: 'above' | 'below' };
export type NextBrief = {
  key: string; title: string; attributes: [string, string][]; stake: NextStake; binding: string;
  carriers: number; evidence: NextEvidence[]; downstream: string; hooks: string[]; refs: NextRef[];
  shape: NextShape; hold: string; watch: NextWatch; caveat: string;
};
export type NextExploreRow = { key: string; dimension: string; group: string; value: string; n_units: number; status: string };
export type NextPlan = {
  status: 'none' | 'done' | 'insufficient' | 'failed';
  created_at?: string; error?: string | null;
  account_spend?: number; window_start?: string | null; window_end?: string | null; n_units?: number; data_through?: string | null;
  allocate?: NextAllocationRow[]; allocate_empty_reason?: string | null;
  make?: NextBrief[]; make_empty_reason?: string | null;
  explore?: NextExploreRow[]; explore_empty_reason?: string | null;
  next_tests?: string[];
};
export type AccountReadFact = { id: string; text: string };
export type AccountReadAnswer = {
  unmappable?: string;
  kind?: string;
  read_as?: string;
  verdict?: 'answerable' | 'directional' | 'not yet';
  answer?: string;
  pattern_ids?: string[];
  facts?: AccountReadFact[];
  tests?: AccountReadTest[];
  what_would_settle_it?: string;
};
