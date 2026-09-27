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
  kind?: 'test' | 'research';
  waiting_on?: string | null;
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
export type ExperimentMedia = { title: string; note?: string; path: string; url: string };
export type Experiment = {
  media?: ExperimentMedia[];
  kind?: 'test' | 'research';
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
  test_read?: { since?: string; arms: TestArm[] } | null;
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
  // The cluster's content findings when it has been read; they replace the generic comparison.
  content?: { title: string; strength: 'holds' | 'context' | 'lead'; p?: number | null }[];
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

// Leads from watching a cluster's 3 best and 3 worst videos side by side (experiment 5). Not graded.
export type ClusterLeadVideo = {
  label: string;
  name: string;
  cpa: number;
  spend: number | null;
  rep_creative_id: string | null;
  poster_url: string | null;
};
export type ClusterLeads = {
  status: 'none' | 'done';
  created_at?: string;
  model?: string;
  videos?: ClusterLeadVideo[];
  answer?: {
    summary: string;
    differences: {
      point: string;
      favours: 'winners' | 'losers';
      have_it: string[];
      lack_it: string[];
      evidence: { video: string; at: string; what: string }[];
    }[];
    per_video: { video: string; read: string }[];
    caveats: string[];
  };
};

// What separates a cluster's best videos from its worst, from blind profiles (experiment 5).
export type ProfileReadVideo = {
  code: string;
  name: string;
  side: 'best' | 'middle' | 'worst' | 'unrankable';
  cpa: number | null;
  spend: number;
  rep_creative_id: string | null;
  poster_url: string | null;
};
export type ProfileReadInsight = {
  title: string;
  body: string;
  numbers: { label: string; value: string }[];
  videos: string[];
  basis: string;
  strength: 'holds' | 'context' | 'lead';
  // The main test's p-value, shown on the card whatever it is; null when there is no test.
  p?: number | null;
};
export type ProfileReadFinding = {
  claim: string;
  feature: string;
  value: string;
  favours: 'winners' | 'losers';
  strength: 'supported' | 'lead' | 'unclear';
  evidence: { video: string; at: string; what: string }[];
  counts: {
    videos_with: string[];
    videos_without: string[];
    scripts_with: number;
    scripts_without: number;
    median_cpa_with: number | null;
    median_cpa_without: number | null;
  } | null;
  flags: string[];
};
export type ProfileRead = {
  status: 'none' | 'done';
  created_at?: string;
  model?: string;
  videos?: Record<string, ProfileReadVideo>;
  reviewed?: ProfileReadInsight[];
  model_read?: {
    read: string;
    findings: ProfileReadFinding[];
    unexplained: { video: string; why: string }[];
    caveats: string[];
    errors: string[];
  };
};

// Variant briefs (/briefs). `facts` is measured by code and never edited; `content` is the
// editable brief.
export type BriefStatus = 'queued' | 'running' | 'done' | 'failed';
export type BriefSummary = {
  id: string;
  title: string;
  status: BriefStatus;
  error: string | null;
  footage_id: string;
  parent_name: string | null;
  version: number;
  created_at: string;
  updated_at: string;
};
export type BriefCandidate = {
  footage_id: string;
  name: string;
  first_day: string;
  full_week: boolean;
  duration_s: number;
  spend_7d: number;
  purchases_7d: number;
  cpa_7d: number;
  roas_7d: number;
  testing_share_7d: number;
  cpa_30d: number | null;
};
export type BriefCandidates = { rule: string; candidates: BriefCandidate[] };
export type BriefSlot = { name: string; start: number; end: number; said: string; shown: string };
export type BriefEdit = {
  segments: [number, number][];
  overlays: { text: string; start: number; end: number }[];
  hold_end: number;
  tight: number[];
};
export type BriefVariant = { code: string; kind: string; name: string; change: string; read_on: string; edit?: BriefEdit | null };
export type BriefRender = {
  id: string;
  status: BriefStatus;
  edit: BriefEdit;
  from_original: boolean;
  url: string | null;
  width: number | null;
  height: number | null;
  duration_s: number | null;
  error: string | null;
  retry_error?: string | null;
  created_at: string;
};
export type BriefBatch = { title: string; variants: BriefVariant[] };
export type BriefScriptLine = { t: string; direction: string; text: string };
export type BriefContent = {
  title: string;
  slots: BriefSlot[];
  findings: string[];
  batches: BriefBatch[];
  script: { name: string; note: string; lines: BriefScriptLine[] };
  launch: string[];
  claims: string[];
  offer?: string;
  dropped?: string[];
};
export type BriefFacts = {
  footage_id: string;
  name: string | null;
  duration_s: number;
  stats: BriefCandidate | null;
  curve: [number, number][];
  steepest_drop: { start: number; end: number; from: number; to: number; ratio: number } | null;
  end_card_offer: boolean;
  timing?: 'words' | 'pauses';
};
export type FocusFinding = { id: string; cluster: string; title: string; body: string; strength: FindingStrength | null; basis: string | null; numbers: { label: string; value: string }[] };
export type Brief = BriefSummary & {
  focus?: FocusFinding | null;
  facts: BriefFacts | null;
  content: BriefContent | null;
  has_original?: boolean;
  renders?: Record<string, BriefRender>;
};

// Generate tab: new openings edited from a prompt by a video model.
export type GenerationStatus = 'queued' | 'running' | 'done' | 'failed';
export type Generation = {
  id: string;
  footage_id: string;
  rep_creative_id: string | null;
  parent_name: string | null;
  prompt: string;
  model: string;
  resolution: string;
  window_s: number;
  status: GenerationStatus;
  stage: string | null;
  estimated_cost_usd: number | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  compare_url: string | null;
  speed?: number | null;
  in_sync_until_s?: number | null;
  swap_at_s?: number | null;
  notes?: string[];
  result_url?: string | null;
  raw_url?: string | null;
};
export type GenerateCandidate = {
  footage_id: string;
  name: string;
  duration_s: number;
  spend_30d: number;
  purchases_30d: number;
  cpa_30d: number | null;
  hook_rate: number | null;
  hook_vs_account: number | null;
  weak_hook: boolean;
  converting: boolean;
};
export type GenerateCandidates = { account_hook_rate: number | null; account_cpa: number | null; candidates: GenerateCandidate[] };
export type GeneratePlan = { window_s: number; cut_s: number | null; width: number; height: number; cost: Record<string, number> };

// Make tab: one place per video for re-edits, generated openings and lines to shoot.
export type MakeReason = 'winner' | 'winner_worn' | 'weak_opening';
export type MakeCard = {
  footage_id: string;
  name: string;
  duration_s: number;
  rep_creative_id: string | null;
  poster_url: string | null;
  reasons: MakeReason[];
  cpa_7d: number | null;
  purchases_7d: number | null;
  roas_7d: number | null;
  cpa_30d: number | null;
  spend_30d: number | null;
  hook_rate: number | null;
  hook_vs_account: number | null;
};
export type MakeInProgress = MakeCard & { briefs: number; generations: number; renders: number; running: boolean; updated_at: string | null };
export type MakeIdeaState = 'idea' | 'working' | 'ready' | 'stale' | 'failed' | 'shoot';
export type MakeIdea = {
  ref: string;
  kind: 'reedit' | 'opening' | 'pickup' | 'script' | 'fix';
  code: string;
  title: string;
  name: string | null;
  change: string | null;
  read_on: string | null;
  finding: string | null;
  cost_label: string;
  cost_usd: number | null;
  decision: 'in' | 'skipped' | null;
  state: MakeIdeaState;
  blockers: string[];
  warnings: string[];
  media_url: string | null;
  result_url?: string | null;
  edit: BriefEdit | null;
  lines?: BriefScriptLine[];
  brief_id?: string;
  generation_id?: string;
  stage?: string | null;
  span?: { start: number; end: number; part: MakePart; output_at: number; how: string } | null;
  part?: MakePart | null;
  aim?: 'weak' | 'strong' | 'even' | null;
};
export type MakePart = 'opening' | 'middle' | 'close';
export type MakeVerdictStep = { step: 'stop' | 'click' | 'buy'; part: MakePart; value: number | null; base: number | null; ratio: number | null; state: 'strong' | 'weak' | 'even' | null };
export type MakeVerdict = {
  kind: 'good' | 'even' | 'weak' | 'unknown';
  headline: string;
  advice?: string;
  cpa?: number | null;
  base_cpa?: number | null;
  steps: MakeVerdictStep[];
  weak: MakePart | null;
  strong: MakePart[];
  /** What it was judged against: its cluster when that has enough videos, else the account. */
  baseline?: { kind: 'cluster' | 'account'; label: string; videos: number };
  /** The same video judged against the account, when the lead verdict is the cluster's. */
  other?: MakeVerdict | null;
  cluster?: string | null;
};
export type MakeFix = {
  key: string;
  part: MakePart;
  kind: 'hook' | 'cutaway' | 'end' | 'line';
  start: number;
  end: number;
  finding_id: string | null;
  finding: string | null;
  tier: 'rule' | 'test' | 'context' | null;
  trait: string;
  untested: boolean;
  read_on: string;
  weak: boolean;
  cost_usd: number;
  attempt: { id: string; status: 'confirm' | 'queued' | 'running' | 'done' | 'failed'; stage: string | null; error: string | null; cost_usd: number | null; prompt: string | null } | null;
};
export type MakeTimeline = { duration: number; sentences: { start: number; end: number; text: string }[]; parts: Record<MakePart, [number, number]> };
export type MakeFocus = FocusFinding & { trait: string | null; direction: 'do' | 'avoid' | null; tier: string | null; found: { start: number; end: number; text: string }[] | null; applied: boolean };
export type MakeBatch = {
  id: string | null;
  status: 'drafting' | 'launched';
  experiment_id: string | null;
  chosen: string[];
  ready: number;
  max: number;
  can_launch: boolean;
  needs: { arms: number; hook_impressions: number; purchases: number; budget_for_purchases: number | null };
};
export type MakeOverview = {
  stages: { drafting: MakeInProgress[]; ready: MakeInProgress[] };
  account_hook_rate: number | null;
  account_cpa: number | null;
  in_progress?: MakeInProgress[];
  worth_working_on?: MakeCard[];
};
export type MakeVideo = {
  verdict: MakeVerdict;
  timeline: MakeTimeline;
  fixes?: MakeFix[];
  focus: MakeFocus | null;
  ideas: MakeIdea[];
  batch: MakeBatch;
  launched: { experiment_id: string | null; title: string | null; launched_at: string | null }[];
  footage_id: string;
  ad_account_id: string;
  name: string;
  duration_s: number;
  rep_creative_id: string | null;
  poster_url: string | null;
  video_url: string | null;
  reasons: MakeReason[];
  winner: BriefCandidate | null;
  hook: { spend_30d: number | null; cpa_30d: number | null; hook_rate: number | null; hook_vs_account: number | null; weak_hook: boolean } | null;
  account_hook_rate: number | null;
  account_cpa: number | null;
  curve: [number, number][];
  steepest_drop: { start: number; end: number; from: number; to: number; ratio: number } | null;
  brief: Brief | null;
  generations: Generation[];
};


// What works (/what-works): findings from inside one cluster only.
export type FindingStrength = 'holds' | 'lead' | 'context';
export type FindingStatus = 'untested' | 'writing' | 'briefed' | 'made';
export type FindingProgress = { brief_id: string; footage_id: string; name: string | null; status: string; renders: number; openings: number; created_at: string };
export type WorksFinding = {
  id: string;
  status: FindingStatus;
  progress: FindingProgress[];
  title: string;
  body: string;
  strength: FindingStrength | null;
  basis: string | null;
  numbers: { label: string; value: string }[];
  examples: { code: string; name: string; poster_url: string | null; rep_creative_id: string | null; spend: number | null; cpa: number | null; side: string | null }[];
  tests?: TestArm[];
  test?: { state: 'confirmed' | 'failed' | 'mixed' | 'testing'; won: number; lost: number; tests: number } | null;
};
export type TestArm = {
  experiment_id?: string;
  test?: string;
  title: string | null;
  ad_name?: string | null;
  finding?: string | null;
  verdict: 'won' | 'lost' | 'no difference' | 'waiting' | 'not found';
  measure: 'stop' | 'click' | 'buy';
  p?: number | null;
  ads?: number;
  why?: string;
  version?: { x: number; n: number; rate: number | null };
  original?: { x: number; n: number; rate: number | null };
};
export type WorksCluster = {
  key: string;
  name: string;
  files: number;
  spend: number;
  share: number;
  purchases: number;
  cpa: number | null;
  hook_rate: number | null;
  read_at: string | null;
  videos: { footage_id: string; name: string; poster_url: string | null; spend: number | null }[];
  findings: WorksFinding[];
};
export type PlayTarget = { code: string; footage_id: string; name: string; poster_url: string | null; spend: number | null; cpa: number | null };
export type PlayFinding = WorksFinding & {
  cluster: string;
  cluster_key: string;
  direction: 'do' | 'avoid';
  tier: 'rule' | 'test' | 'context' | 'story';
  trait: string;
  scripts: [number, number] | null;
  targets: PlayTarget[];
};
export type PlayChoice = {
  key: string;
  name: string;
  element: string;
  question: string;
  verdict: 'do' | 'avoid' | 'mixed' | 'none';
  section: 'rule' | 'mixed' | 'test' | 'context' | 'story';
  spend: number;
  step: 'stop' | 'click' | 'buy' | null;
  headline: { label: string; value: string } | null;
  confidence: { tier: PlayFinding['tier']; scripts: [number, number] | null; p: number | null };
  stake: { spend: number; videos: number; direction: 'do' | 'avoid' } | null;
  findings: PlayFinding[];
};
export type PlayGridRow = { key: string; name: string; section: PlayChoice['section']; cells: Record<string, { direction: 'do' | 'avoid'; tier: PlayFinding['tier'] }> };
export type WhatWorks = {
  window: string;
  span: { start: string; end: string } | null;
  min_spend: number;
  clusters: WorksCluster[];
  playbook: { created_at: string; choices: PlayChoice[]; kinds: string[]; grid: PlayGridRow[] } | null;
  creator_scripts?: CreatorScriptRow[];
};
export type CreatorScript = {
  cluster: string;
  title: string;
  note: string;
  lines: { t: string; direction: string; text: string }[];
  follows: { id: string; how: string; title: string }[];
  missed: { id: string; title: string }[];
  claims: string[];
  dropped: string[];
  from_videos: { footage_id: string; name: string | null; cpa: number | null; watched?: boolean }[];
  rules: number;
  closest?: { footage_id: string; name: string | null; ratio: number } | null;
  presenter?: boolean;
  blockers?: string[];
  too_close?: boolean;
  compared_with?: number;
  created_at: string | null;
};
export type ScriptVideo = {
  id: string;
  status: 'queued' | 'running' | 'confirm' | 'blocked' | 'done' | 'failed';
  stage: string | null;
  error: string | null;
  url: string | null;
  checks: {
    videos: number;
    compared_with_frames: number;
    too_alike: boolean;
    shots: { shot: number; closest_bits: number; too_alike: boolean }[];
    mirrored?: boolean;
    label_readable?: boolean;
    heard?: string;
    meant?: string;
    said_right?: number | null;
  } | null;
  estimated_cost_usd: number | null;
  clip_cost_usd: number | null;
  clip_plan: { shots: number; seconds: number; usd: number; video_seconds: number } | null;
  shots: { prompt: string; product: boolean; lines: number[] }[];
  voice_url: string | null;
  product_url: string | null;
  prompt_text?: string | null;
  mode?: 'talking_head' | 'voiceover' | 'text_only' | null;
  created_at: string;
  experiment_id: string | null;
};
export type CreatorScriptRow = {
  cluster: string;
  name: string;
  files: number;
  spend: number;
  cpa?: number | null;
  script: CreatorScript | null;
  makeable?: boolean;
  estimate?: { seconds: number; prep_usd: number; mode?: 'talking_head' | 'voiceover' | 'text_only' | null } | null;
  video?: ScriptVideo | null;
};
