export type Paginated<T> = { items: T[]; total: number; total_all?: number; total_active?: number; window?: { start: string; end: string } | null };
export type Account = {
  id: string;
  platform: string;
  platform_account_id: string;
  name: string;
  currency: string;
  status: 'active' | 'paused' | 'disconnected';
  sync_cursor: Record<string, unknown>;
  connection_status: 'pending' | 'syncing' | 'ready' | 'error' | null;
  connection_error: string | null;
};
export type MetaConnection = {
  connected: boolean;
  status: 'pending' | 'syncing' | 'ready' | 'error' | null;
  error: string | null;
  last_synced_at: string | null;
};
// Both hops between Meta and the app: Fivetran's copy of Meta (source) and the app's own tables.
export type SyncJob = {
  id: string;
  kind: 'sync' | 'first_sync';
  status: 'pending' | 'running' | 'done' | 'dead';
  retrying: boolean;
  error: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
};
export type SyncStatus = {
  connection: { status: 'pending' | 'syncing' | 'ready' | 'error'; error: string | null; last_synced_at: string | null } | null;
  source?: { ads: number; metrics_through: string | null; has_reports: boolean; delivered_at: string | null } | null;
  app?: { ads: number; metrics_through: string | null };
  last_sync?: SyncJob | null;
  auto_sync?: { enabled: boolean; next_run_at: string | null };
  backlog?: { files: number; tagging: number };
};
export type TagStatus = {
  reason: 'no_asset' | 'unresolved' | 'video_tagging_off' | 'already_tagged' | 'below_threshold' | null;
  detail: string | null;
  impressions?: number | null;
  plays?: number | null;
};
export type Coverage = { files?: number; tagged_files?: number; spend_share?: number | null };
export type Creative = {
  tag_status?: TagStatus;
  id: string;
  ad_account_id: string;
  platform_creative_id: string;
  format: 'image' | 'video' | 'carousel' | 'unresolved';
  headline: string | null;
  primary_text: string | null;
  cta_type: string | null;
  link_url: string | null;
  primary_asset_url: string | null;
  primary_asset_mime?: string | null;
  first_seen_at: string;
  distance?: number;
  spend_30d: number;
  ctr_30d?: number | null;
  creative_count?: number;
  ad_count?: number;
  copy_variants?: number;
  ad_name?: string | null;
  poster_url?: string | null;
  totals?: GridRates;
  baseline?: GridRates;
};
// A grid card's 30-day numbers, and the same numbers pooled over the rest of its account and format.
export type GridRates = {
  spend: number;
  impressions: number;
  link_clicks: number;
  purchases: number;
  hook_rate: number | null;
  link_ctr: number | null;
  cpa: number | null;
  roas: number | null;
};
// 30-day totals for one ad, one copy variant, or a whole footage group.
export type Totals = { spend: number; impressions: number; clicks: number; purchases: number; ctr: number | null; cpa: number | null; roas: number | null };
export type FootageAd = Totals & {
  id: string;
  creative_id: string;
  platform_ad_id: string;
  name: string | null;
  status: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  adset_id: string | null;
};
export type CopyVariant = Totals & { headline: string | null; primary_text: string | null; creative_ids: string[] };
// Every creative running the same file as the one asked for, itself included.
export type Footage = { creative_ids: string[]; ads: FootageAd[]; copy_variants: CopyVariant[] };
export type Scope = 'footage' | 'creative';
// Headline numbers for the diagnosis window, and the same numbers pooled over every other
// creative of the format in the account.
export type SummaryRates = {
  spend: number;
  impressions: number;
  link_clicks: number;
  purchases: number;
  cpm: number | null;
  hook_rate: number | null;
  link_ctr: number | null;
  click_to_purchase: number | null;
  cpa: number | null;
  aov: number | null;
  roas: number | null;
};
export type CreativeSummary = { window_start: string; window_end: string; totals: SummaryRates; baseline: SummaryRates; baseline_creatives: number };
// A graded account-wide main effect whose cell includes this creative's file. Evidence about
// the account, shown beside the creative; it never measures the attribute within the file.
export type CreativePattern = {
  has: boolean;
  idea: number;
  dimension: string;
  value: string;
  metric: 'hook_rate' | 'link_ctr' | 'thruplay_rate' | 'cpa' | 'purchases_per_1k';
  grade: 'strong' | 'suggestive';
  change: number;
  n_cell: number;
  n_complement: number;
  cell_value: number | null;
  comp_value: number | null;
  bin_edges: [number, number] | null;
  confounds: string[];
  prescribable?: boolean;
  window_start?: string;
  window_end?: string;
};
export type MetricDay = {
  day: string;
  spend: number;
  impressions: number;
  clicks: number;
  link_clicks: number;
  video_3s_views: number;
  conversions: number;
  conversion_value: number;
  ctr: number | null;
  cpa: number | null;
  roas: number | null;
};
export type TagKind = 'numeric' | 'single' | 'multi' | 'ordered' | 'open' | 'text' | 'series';
export type Tag = {
  dimension: string;
  kind: TagKind;
  value: string;
  raw_value: string | null;
  confidence: number;
  evidence: string;
  t_start_s: number | null;
  ordinal: number | null;
};
export type Dimension = { name: string; group: string; kind: TagKind; applies_to: string[]; values: string[] | null; description: string };
export type DimensionValue = { value: string; count: number };
export type DiagnosisStatus = 'strong' | 'on_par' | 'weak' | 'not_applicable' | 'not_enough_data' | 'too_young' | 'not_measured';
export type DiagnosisMetric = { key: string; label: string; value: number | null; baseline: number | null; lift: number | null; note: string };
export type DiagnosisCurvePoint = { p: number; share: number | null; baseline_share: number | null };
// Meta's per-second play curve (0–14s one point per second, then wider buckets to 60s+).
export type PlayCurvePoint = { start_s: number; end_s: number | null; share: number | null; baseline_share: number | null };
export type PlayCurve = { points: PlayCurvePoint[]; plays: number; baseline_plays: number; baseline_label: string | null; duration_s: number | null };
export type DiagnosisCurve = {
  points: DiagnosisCurvePoint[];
  drop_segment: [number, number] | null;
  avg_watch_s: number | null;
  duration_s: number | null;
  baseline_label: string | null;
};
export type DiagnosisStep = {
  key: string;
  label: string;
  rate: number | null;
  baseline_rate: number | null;
  lift: number | null;
  numerator: number;
  denominator: number;
  readable: boolean;
};
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
export type AccountReadTest = {
  dimension: string;
  value: string;
  group: string | null;
  metric: string;
  kind: 'per_unit' | 'pooled';
  n_cell: number;
  n_complement: number;
  cell_value: number;
  comp_value: number;
  change: number;
  p: number;
  grade: string;
  confounds: string[];
  cell_shas: string[];
  bin_edges: [number, number] | null;
  cell_purchases: number | null;
  pid?: string;
};
export type AccountReadFinding = {
  title: string;
  layer: string;
  paragraph: string;
  mechanism: string;
  exemplars: string[];
  exception: string;
  why_non_obvious: string;
  pattern_ids: string[];
  confidence: string;
};
export type AccountReadText = {
  overview: string;
  findings: AccountReadFinding[];
  entangled: string;
  no_evidence: string;
  next_tests: string[];
  data_needed: string[];
};
export type AccountReadUnit = {
  sha256: string;
  headline: string;
  spend: number;
  creative_ids: string[];
  rep_creative_id: string | null;
  duration_s: number | null;
  impressions: number;
  purchases: number;
  video_3s_views?: number | null;
  link_clicks?: number | null;
  poster_url?: string | null;
};
export type AccountRead = {
  status: 'none' | 'done' | 'insufficient' | 'rejected' | 'failed';
  n_units?: number;
  created_at?: string;
  window_start?: string | null;
  window_end?: string | null;
  summary?: Record<string, unknown>;
  tests?: AccountReadTest[];
  sibling_tests?: AccountReadTest[];
  overlaps?: unknown[];
  stratified?: unknown[];
  retention_by_reveal?: Record<string, unknown>;
  units?: AccountReadUnit[];
  coverage?: import('./types').Coverage | null;
  read?: AccountReadText | null;
  error?: string | null;
  newer_failed_error?: string | null;
};
export type NextDecline = { metric: 'cpa' | 'ctr'; baseline: number; observed: number; ratio: number; capped: boolean };
export type NextStake = { amount: number; share: number; window: 'window' | '30d'; basis: string };
export type NextAllocationRow = {
  key: string;
  kind: 'fix' | 'replace' | 'scale';
  creative_id: string;
  footage_id: string | null;
  headline: string;
  rep_creative_id: string | null;
  stake: NextStake;
  verdict: string;
  detail: string;
  broken_layer: string | null;
  decline: NextDecline | null;
  poster_url?: string | null;
};
export type NextCardRef = {
  footage_id: string;
  title: string;
  rep_creative_id: string | null;
  spend_30d: number;
  value: number | null;
  value_label: string | null;
  poster_url?: string | null;
};
export type NextCardWritten = { title: string; bleed: string; keep: string; cause: string; copy_this: string; job: string };
export type NextCardLines = {
  headline: string;
  finding: string;
  position: string;
  evidence: string;
  money: string;
  /** What the change costs, when another graded finding argues against it. Shown on the card
   *  face: a reader comparing this page with Insights must not meet a bare contradiction. */
  trade: string | null;
  mechanism: string;
  moment: string | null;
  job: string;
};
export type NextReadBack = { age_days: number; outcome: string; detail: string; adoption: string | null };
export type NextCard = {
  key: string;
  verb: 'recut' | 'change' | 'reshoot';
  footage_id: string;
  rep_creative_id: string | null;
  title: string;
  spend_30d: number;
  // The account read's own citation. The same P-id /insights prints for this finding.
  pid: string;
  layer: string;
  grade: string;
  confidence: string;
  metric: string;
  metric_name: string;
  change: number;
  label: string;
  add: boolean;
  carries_money: boolean;
  at_s: number | null;
  side_value: string;
  other_value: string;
  // The two group averages as the card compares them, plus this ad's own when it has enough
  // purchases to have one.
  wrong_value: string;
  right_value: string;
  own_value: number | null;
  own_value_label: string | null;
  lines: NextCardLines;
  // Absent when narration is paused or the card kept its template wording.
  written: NextCardWritten | null;
  refs: NextCardRef[];
  read_back: NextReadBack | null;
  poster_url?: string | null;
};
export type NextPlan = {
  status: 'none' | 'done' | 'insufficient' | 'failed';
  created_at?: string;
  error?: string | null;
  account_spend?: number;
  window_start?: string | null;
  window_end?: string | null;
  n_units?: number;
  data_through?: string | null;
  allocate?: NextAllocationRow[];
  allocate_empty_reason?: string | null;
  recut?: NextCard[];
  recut_empty_reason?: string | null;
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
// The Library: the account as Meta structures it. Every node's numbers are the sum of its own
// ads' 30-day totals, never a file's footage-wide totals.
export type LibraryCounts = { ads: number; ads_live: number; ads_spent: number };
export type LibraryAdSet = LibraryCounts & { id: string | null; name: string | null; totals: GridRates };
export type LibraryCampaign = LibraryCounts & { id: string | null; name: string | null; ad_account_id: string; totals: GridRates; adsets: LibraryAdSet[] };
export type LibraryTree = { campaigns: LibraryCampaign[]; totals: GridRates | null; window: { start: string; end: string } | null };
export type LibraryAd = {
  id: string;
  ad_account_id: string;
  platform_ad_id: string;
  name: string | null;
  status: string | null;
  effective_status: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  adset_id: string | null;
  adset_name: string | null;
  creative_id: string;
  format: Creative['format'];
  headline: string | null;
  primary_text: string | null;
  primary_asset_url: string | null;
  primary_asset_mime: string | null;
  poster_url: string | null;
  totals: GridRates;
  baseline: GridRates;
};
export type LibraryDelivery = 'spent' | 'live' | 'all';
export type LibraryAds = { items: LibraryAd[]; total: number; counts: Record<LibraryDelivery, number> };
// Experiments: a question tested on a fixed set of files, one visible step at a time.
export type ExperimentStepStatus = 'pending' | 'waiting' | 'running' | 'done' | 'failed';
export type ExperimentStep = {
  key: string;
  title: string;
  detail: string;
  status: ExperimentStepStatus;
  waiting_on: string | null;
  progress_done: number;
  progress_total: number;
  cost_usd: number;
  result_note: string | null;
  started_at: string | null;
  finished_at: string | null;
};
export type ExperimentSummary = {
  id: string;
  slug: string;
  title: string;
  question: string;
  status: string;
  created_at: string;
  files: number;
  steps: number;
  steps_done: number;
  current_step: string | null;
  current_status: ExperimentStepStatus | null;
};
export type ExperimentItem = {
  id: string;
  creative_id: string | null;
  label: string;
  label_source: string | null;
  format: 'video' | 'image';
  duration_s: number | null;
  name: string | null;
  poster_url: string | null;
  format_answer: string | null;
  group_id: string | null;
};
export type ExperimentScore = { files: number; purity: number | null; completeness: number | null; ari: number | null };
export type ExperimentRule = { title: string; rule: string; decided_by: 'you' | 'claude'; why: string; effect: string; dropped: string[] };
export type Experiment = {
  id: string;
  title: string;
  question: string;
  method: string;
  rules: ExperimentRule[];
  status: string;
  created_at: string;
  steps: ExperimentStep[];
  items: ExperimentItem[];
  run: string | null;
  groups: { id: string; name: string; definition: string | null }[];
  score: ExperimentScore | null;
};
// One ad set read three ways (cs.experiments.adset_read). A rate carries a 95% range and
// whether it clears the floor to be read at all.
export type RangedRate = { value: number | null; low: number | null; high: number | null; readable: boolean };
export type AdSetRates = {
  spend: number;
  impressions: number;
  link_clicks: number;
  purchases: number;
  hook_rate: RangedRate;
  hold_rate: RangedRate;
  link_ctr: RangedRate;
  cpa: RangedRate;
  roas: { value: number | null; readable: boolean };
};
export type SiblingVerdict = 'better' | 'worse' | 'level' | null;
export type AdSetRow = {
  id: string;
  creative_id: string | null;
  name: string | null;
  duration_s: number | null;
  days: number;
  poster_url: string | null;
  totals: AdSetRates;
  siblings: AdSetRates;
  vs_siblings: Record<'hook_rate' | 'hold_rate' | 'link_ctr' | 'cpa', SiblingVerdict>;
  // Every other file of the same format in the account, over the days this file ran here.
  account: AdSetRates | null;
  vs_account: Record<'hook_rate' | 'hold_rate' | 'link_ctr' | 'cpa', SiblingVerdict>;
  retention: { label: string; share: number }[] | null;
  why: AdWhy;
};
// Why one ad did what it did against the rest of its ad set (cs.experiments.why).
export type WhyStepKey = 'cpm' | 'stop' | 'click' | 'ctr' | 'buy';
export type WhyStep = {
  key: WhyStepKey;
  label: string;
  mine: number | null;
  theirs: number | null;
  low?: number | null;
  high?: number | null;
  push: number | null;
  clear: boolean;
};
export type WhyOpening = { words?: string; text?: string[]; scenes?: string[]; face?: boolean; product?: boolean };
export type AdWhy = {
  decomposition: {
    cpa: number | null;
    siblings_cpa: number | null;
    cpa_gap: number | null;
    aov: number | null;
    siblings_aov: number | null;
    steps: WhyStep[];
    summary: string;
  } | null;
  main_step: WhyStepKey | null;
  spend_share: number | null;
  siblings: number;
  opening: WhyOpening | null;
  sibling_openings: (WhyOpening & { id: string; name: string | null; stop_rate: number | null })[];
  step_tags: Record<'click' | 'ctr' | 'buy', { dimension: string; value: string; siblings_with: number; unreliable: boolean }[]>;
  sibling_retention: { label: string; share: number }[] | null;
  audience: { segment: string; share: number; siblings_share: number; cpm: number | null }[];
  described: { hook: string | null; angle: string | null; funnel_stage: string | null } | null;
};
export type AdSetBest = {
  id: string;
  basis: string;
  compared_with: number;
  has: { dimension: string; value: string; others: number }[];
  lacks: { dimension: string; value: string; others: number }[];
  described: { format: string | null; hook: string | null; angle: string | null } | null;
};
// A decision the read supports, crisp enough to act on without the tables.
// One number compared twice: with the rest of its group (which colours it) and with the account.
// A verdict is null when the gap is under 15% or not clear of noise.
export type AdSetStat = { label: string; mine: string; group: string; group_good: boolean | null; account: string; account_good: boolean | null };
export type AdSetCard = {
  kind: 'scale' | 'cut' | 'recut' | 'fix_middle' | 'fix_promise';
  title: string;
  stats: AdSetStat[];
  why: string | null;
  ad: string;
  compare_with: string | null;
  also?: string[];
  ads?: ({ ad: string; name: string } & AdSetStat)[];
  spend_share: number | null;
};
export type AdSetHeadline = { title: string; stats: { label: string; mine: string; theirs: string; status: string }[] };
export type AdSetFormatRead = {
  cards: AdSetCard[];
  headline: AdSetHeadline | null;
  format: 'video' | 'image';
  since: string | null;
  until: string | null;
  files: number;
  delivered: number;
  pooled: AdSetRates;
  diagnosis: Diagnosis | null;
  rows: AdSetRow[];
  best: AdSetBest | null;
};
export type AdSetRead = { label: string | null; noun?: string; formats: AdSetFormatRead[]; cluster?: { key: string; name: string; files: number } };
// Patterns across ad sets (cs.experiments.paired): one comparison per ad set that holds files
// with the tag and files without it, on that ad set's own delivery.
export type PairedComparison = { label: string; has: number; lacks: number; with: number; without: number; gap: number; won: boolean };
export type PairedPattern = {
  format: 'video' | 'image';
  dimension: string;
  value: string;
  metric: 'hook_rate' | 'link_ctr' | 'cpa';
  metric_label: string;
  higher_is_better: boolean;
  adsets: number;
  wins: number;
  median_gap: number;
  p: number;
  q: number;
  grade: 'consistent' | 'leaning' | 'mixed';
  comparisons: PairedComparison[];
};
export type PairedPatterns = {
  tested: number;
  expected_by_chance: number;
  adsets_used: { video: number; image: number };
  duplicates: { label: string; same_as: string }[];
  patterns: PairedPattern[];
};
// The themes view: an experiment's videos grouped by one axis, with pooled first-7-day numbers.
export type ThemeFile = {
  id: string;
  creative_id: string | null;
  name: string | null;
  duration_s: number | null;
  poster_url: string | null;
  reason: string | null;
  spend: number;
};
export type ThemeGroup = {
  key: string;
  name: string;
  files: ThemeFile[];
  totals: {
    files: number;
    spend: number;
    purchases: number;
    hook_rate: number | null;
    link_ctr: number | null;
    cpa: number | null;
    median_duration_s?: number | null;
  };
};
export type ThemeView = {
  axis: string;
  axes: { key: string; label: string }[];
  groups: ThemeGroup[];
  unlabelled: number;
  total: number;
  all: number;
  min_spend: number;
  window?: 'first_7d' | 'last_30d';
  idle?: number;
  span?: { start: string; end: string } | null;
};

// Clusters against each other, graded the way the Insights tab grades a pattern.
export type ClusterMetric = {
  metric: 'hook_rate' | 'thruplay_rate' | 'link_ctr' | 'purchases_per_1k' | 'cpa';
  label: string;
  mine: number;
  theirs: number;
  change: number;
  better: boolean;
  p: number;
  grade: 'strong' | 'suggestive' | 'directional' | 'thin';
  n_mine: number;
  n_theirs: number;
  kind: 'per_unit' | 'pooled';
  confounds: string[];
};
export type ClusterPooled = { videos: number; spend: number; purchases: number; cpa: number | null; hook_rate: number | null };
export type ClusterCard = {
  key: string;
  kind: 'scale' | 'attention_not_sale' | 'cut' | 'weak_steps' | 'strong_steps';
  title: string;
  // The same four numbers on every card, cluster beside the whole account.
  stats: {
    metric: ClusterMetric['metric'];
    label: string;
    mine: number | null;
    account: number | null;
    grade: ClusterMetric['grade'] | null;
    better: boolean | null;
    holds: boolean;
  }[];
  why: string | null;
  totals: ClusterPooled;
  thumbs: { id: string; creative_id: string | null; name: string | null; poster_url: string | null; duration_s: number | null }[];
};
export type ClusterCompare = {
  cards: ClusterCard[];
  account: ClusterPooled & { thruplay_rate: number | null; link_ctr: number | null };
  level: string[];
  too_small: string[];
  vs_rest: { key: string; name: string; totals: ClusterPooled; rest: ClusterPooled; metrics: (ClusterMetric | null)[]; too_small: boolean }[];
  span: { start: string; end: string } | null;
  window: string;
};

// The Insights read inside one cluster (experiment 5): a written read when the cluster has more
// suggestive results than chance, else just the counts.
export type ClusterInsights = Omit<AccountRead, 'status'> & {
  status: AccountRead['status'] | 'chance';
  stats?: { clean: number; suggestive: number; chance: number; p: number; beyond: boolean } | null;
};
