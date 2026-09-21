import { AlertCircle, CheckCircle2, ChevronRight, Clock, Lightbulb, Lock } from 'lucide-react';
import { cn } from 'cn';
import type { Diagnosis, DiagnosisAudienceSlice, DiagnosisCurve, DiagnosisMetric, DiagnosisPlacement, DiagnosisStatus, DiagnosisStep, DiagnosisTier } from '@/lib/types';

// Type system (see docs/superpowers/specs/2026-09-12-creative-diagnosis-panel-design.md and the
// 2026-09-12 readability revision): four sizes only -- 24px metric values, 16px names and step
// values, 14px everything else, 12px for curve axis ticks. Hierarchy comes from weight, never
// from shrinking text. Two text colors: foreground, and muted-foreground only at 14px and up.
// Color is reserved for status. The panel lives in a ~750px column, so every row is
// [label column | evidence column] and the evidence always gets the full remaining width.

const pct = (x: number) => `${Math.round(x * 100)}%`;
const pct1 = (x: number) => `${(x * 100).toFixed(1)}%`;
const signed = (x: number) => `${x >= 0 ? '+' : '−'}${Math.round(Math.abs(x) * 100)}%`;

function fmtValue(m: DiagnosisMetric, v: number | null): string {
  if (v == null) return '—';
  if (m.key === 'cpa') return `$${Math.round(v)}`;
  if (m.key === 'frequency') return v.toFixed(1);
  if (m.key === 'link_ctr' || m.key === 'engagement_rate') return pct1(v);
  return pct(v);
}

const statusLabels: Record<DiagnosisStatus, string> = {
  strong: 'Strong',
  on_par: 'On par',
  weak: 'Weak',
  not_applicable: 'Not applicable',
  not_enough_data: 'Not enough data',
  too_young: 'Too young',
  not_measured: 'Not measured',
};

function statusLabel(tier: DiagnosisTier): string {
  if (tier.key === 'fatigue' && tier.status === 'weak') return 'Early decay';
  if (tier.key === 'fatigue' && tier.status === 'on_par') return 'Stable';
  return statusLabels[tier.status];
}

const pillClass: Record<DiagnosisStatus, string> = {
  strong: 'bg-success/10 text-success',
  on_par: 'bg-muted text-muted-foreground',
  weak: 'bg-destructive/10 text-destructive',
  not_applicable: 'border border-border text-muted-foreground',
  not_enough_data: 'border border-border text-muted-foreground',
  too_young: 'border border-border text-muted-foreground',
  not_measured: 'border border-dashed border-muted-foreground/50 text-muted-foreground',
};

const railClass: Record<DiagnosisStatus, string> = {
  strong: 'bg-success/10 text-success',
  on_par: 'bg-muted text-muted-foreground',
  weak: 'bg-destructive text-primary-foreground',
  not_applicable: 'bg-muted text-muted-foreground',
  not_enough_data: 'bg-muted text-muted-foreground',
  too_young: 'bg-muted text-muted-foreground',
  not_measured: 'border border-dashed border-muted-foreground/50 text-muted-foreground',
};

function liftClass(tier: DiagnosisTier, m: DiagnosisMetric): string {
  if (m.lift == null) return 'text-muted-foreground';
  if (tier.key === 'fatigue') return Math.abs(m.lift) >= 0.15 ? 'text-chart-2' : 'text-muted-foreground';
  return m.lift >= 0.15 ? 'text-success' : m.lift <= -0.15 ? 'text-destructive' : 'text-muted-foreground';
}

// The API's lift is signed so that positive always means better, which for a cost means the
// number went down. A media buyer reads "+4%" on cost per purchase as "it got dearer", so
// the page prints the arithmetic change and leaves "better or worse" to the colour.
const COST_METRICS = new Set(['cpa']);
const shownChange = (m: DiagnosisMetric) => (COST_METRICS.has(m.key) ? -(m.lift ?? 0) : (m.lift ?? 0));

function Pill({ tier, className }: { tier: DiagnosisTier; className?: string }) {
  return (
    <span className={cn('inline-flex h-6 w-fit items-center gap-1.5 rounded-4xl px-2.5 text-sm font-medium', className ?? pillClass[tier.status])}>
      {tier.status === 'not_measured' && <Lock className="size-3" aria-hidden="true" />}
      {statusLabel(tier)}
    </span>
  );
}

function MetricBlock({ tier, m }: { tier: DiagnosisTier; m: DiagnosisMetric }) {
  const compare = tier.key === 'fatigue' ? 'was' : 'vs';
  return (
    <div className="flex flex-col gap-1.5" title={m.note}>
      <span className="text-sm text-muted-foreground">{m.label}</span>
      <div className="flex items-baseline gap-2.5">
        <span className="text-2xl leading-none font-semibold tabular-nums">{fmtValue(m, m.value)}</span>
        {m.baseline != null && <span className="text-sm text-muted-foreground tabular-nums">{`${compare} ${fmtValue(m, m.baseline)}`}</span>}
        {m.lift != null && <span className={cn('text-sm font-semibold tabular-nums', liftClass(tier, m))}>{signed(shownChange(m))}</span>}
      </div>
    </div>
  );
}

const tickName = (p: number) => (p === 0 ? 'Start' : p === 100 ? 'End' : `${p}%`);

function RetentionCurve({ curve }: { curve: DiagnosisCurve }) {
  const W = 320;
  const H = 96;
  const top = 6;
  const bottom = 90;
  const n = curve.points.length;
  const x = (i: number) => (n > 1 ? (i / (n - 1)) * W : 0);
  const y = (s: number | null) => (s == null ? bottom : bottom - s * (bottom - top));
  const line = (key: 'share' | 'baseline_share') => curve.points.map((p, i) => `${x(i)},${y(p[key])}`).join(' ');
  const seg = curve.drop_segment;
  const segIdx = seg ? [curve.points.findIndex((p) => p.p === seg[0]), curve.points.findIndex((p) => p.p === seg[1])] : null;
  return (
    <div className="flex w-full flex-col gap-2">
      <span className="text-sm text-muted-foreground">
        Share of plays still watching{curve.baseline_label ? ` · dashed line is the ${curve.baseline_label}` : ''}
      </span>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block" role="img" aria-label="Watch-through curve">
        {segIdx && segIdx[0] >= 0 && segIdx[1] >= 0 && (
          <rect x={x(segIdx[0])} y={0} width={x(segIdx[1]) - x(segIdx[0])} height={bottom} className="fill-destructive/10" />
        )}
        <line x1="0" y1={bottom} x2={W} y2={bottom} className="stroke-border" strokeWidth="1" />
        <polyline fill="none" className="stroke-muted-foreground" strokeWidth="1.5" strokeDasharray="4 3" points={line('baseline_share')} />
        <polyline fill="none" className="stroke-destructive" strokeWidth="2.5" strokeLinejoin="round" points={line('share')} />
        {curve.points.map((p, i) => (
          <circle key={p.p} cx={x(i)} cy={y(p.share)} r="3.5" className="fill-destructive" />
        ))}
      </svg>
      <div className="grid tabular-nums" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
        {curve.points.map((p, i) => {
          const inDrop = seg != null && (p.p === seg[0] || p.p === seg[1]);
          const align = i === 0 ? 'text-left' : i === n - 1 ? 'text-right' : 'text-center';
          return (
            <div key={p.p} className={cn('flex flex-col gap-0.5', align)}>
              <span className="text-xs text-muted-foreground">{tickName(p.p)}</span>
              <span className={cn('text-sm font-semibold', inDrop && 'text-destructive')}>{p.share == null ? '—' : pct(p.share)}</span>
            </div>
          );
        })}
      </div>
      <span className="text-sm text-muted-foreground">
        {curve.avg_watch_s != null && `Average watch ${curve.avg_watch_s.toFixed(1)}s${curve.duration_s != null ? ` of ${Math.round(curve.duration_s)}s` : ''}. `}
        Dashed line is the account baseline.
      </span>
    </div>
  );
}

function StepChain({ steps }: { steps: DiagnosisStep[] }) {
  return (
    <div className="flex w-full flex-wrap items-stretch gap-2">
      {steps.map((s) => (
        <div
          key={s.key}
          className={cn('flex min-w-[8.5rem] flex-1 flex-col gap-1 rounded-lg border px-3 py-2.5', s.readable ? 'border-border' : 'border-dashed border-muted-foreground/40')}
        >
          <span className="text-sm text-muted-foreground">{s.label}</span>
          {s.readable && s.rate != null ? (
            <div className="flex items-baseline gap-2">
              <span className="text-base font-semibold tabular-nums">{pct(s.rate)}</span>
              {s.baseline_rate != null && <span className="text-sm text-muted-foreground tabular-nums">{`vs ${pct(s.baseline_rate)}`}</span>}
            </div>
          ) : (
            <span className="text-sm text-muted-foreground tabular-nums">{s.numerator > 0 ? `${s.numerator} · below floor` : '—'}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function fitText(x: number): string {
  return x >= 0.15 ? 'text-success' : x <= -0.15 ? 'text-destructive' : 'text-muted-foreground';
}

// Three is what fits the evidence column in one row at this card width, so the row always
// reads like the conversion step chain instead of leaving a stretched orphan on a second line.
const PLACEMENT_CARDS = 3;

// Fit, not lift: how far the rate sits from this creative's own placement average once the
// comparison set's pattern for that placement is removed. Meta chose the mix, so a level
// comparison would partly measure the optimiser's allocation. Fit is zero-sum across a
// creative's placements -- it says where the creative travels, never how well it performed.
//
// Same two-line card as the conversion step chain: what it is, then the number and what to
// read it against. The verdict sentence above already names the decisive placement, so these
// only have to substantiate it -- the full set stays in the API payload and the insight brief.
function PlacementCards({ placements, format }: { placements: DiagnosisPlacement[]; format: Diagnosis['format'] }) {
  const video = format === 'video';
  const fitOf = (p: DiagnosisPlacement) => (video ? p.hook_fit : p.ctr_fit);
  const rated = placements.filter((p) => fitOf(p) != null);
  const shown = rated.slice(0, PLACEMENT_CARDS);
  const notes = [
    rated.length - shown.length > 0 ? `${rated.length - shown.length} more` : null,
    placements.length - rated.length > 0 ? `${placements.length - rated.length} too thin to rate` : null,
  ].filter(Boolean);
  if (shown.length === 0) return null;
  return (
    <div className="flex w-full flex-col gap-2">
      <div className="flex w-full flex-wrap items-stretch gap-2">
        {shown.map((p) => {
          const fit = fitOf(p) as number;
          const rate = video ? p.hook_rate : p.link_ctr;
          return (
            <div
              key={`${p.platform}/${p.position}`}
              className="flex min-w-[8.5rem] flex-1 flex-col gap-1 rounded-lg border border-border px-3 py-2.5"
              title={rate != null ? `${video ? 'Hook rate' : 'Link CTR'} ${video ? pct(rate) : pct1(rate)}` : undefined}
            >
              <span className="min-w-0 truncate text-sm text-muted-foreground">{p.label}</span>
              <div className="flex items-baseline gap-2">
                <span className="text-base font-semibold tabular-nums">{p.share != null ? pct(p.share) : '—'}</span>
                <span className={cn('text-sm font-semibold tabular-nums', fitText(fit))}>{signed(fit)}</span>
              </div>
            </div>
          );
        })}
      </div>
      {notes.length > 0 && <span className="text-sm text-muted-foreground">{`+ ${notes.join(' · ')}`}</span>}
    </div>
  );
}

// Both numbers are shares, so the gap is in percentage points -- a percentage of a percentage
// would not mean anything. It stays uncoloured: who Meta found is evidence about the creative,
// not a verdict on it, and colour in this panel is reserved for status.
const points = (x: number) => `${x >= 0 ? '+' : '−'}${Math.round(Math.abs(x) * 100)}\u00a0pts`;

// The slice that diverges most from the peer group -- the part a reader could not guess.
function AudienceLine({ audience }: { audience: DiagnosisAudienceSlice[] }) {
  const named = audience.filter((s) => s.age !== 'other');
  if (named.length === 0) return null;
  const top = named.reduce((a, b) => (Math.abs(b.share - b.baseline_share) > Math.abs(a.share - a.baseline_share) ? b : a));
  return (
    <p className="text-sm text-muted-foreground">
      {'Who Meta found: '}
      <span className="font-semibold text-foreground">{top.label}</span>
      {` took ${pct(top.share)} of delivery, against ${pct(top.baseline_share)} for peers (${points(top.share - top.baseline_share)}).`}
    </p>
  );
}

function TierBody({ tier, broken, format, evidence }: { tier: DiagnosisTier; broken: boolean; format: Diagnosis['format']; evidence?: React.ReactNode }) {
  const weak = tier.status === 'weak';
  // The verdict above already carries the broken layer's decision word for word.
  const text = broken ? null : weak ? tier.decision : tier.reason;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {text && <p className={cn('max-w-prose text-sm leading-relaxed', weak ? 'font-semibold' : 'text-muted-foreground')}>{text}</p>}
      {/* Attention's hook rate and link CTR are already in the header, right above. */}
      {tier.key !== 'attention' && tier.metrics.length > 0 && (
        <div className="flex flex-wrap gap-x-10 gap-y-4">
          {tier.metrics.map((m) => (
            <MetricBlock key={m.key} tier={tier} m={m} />
          ))}
        </div>
      )}
      {tier.curve && <RetentionCurve curve={tier.curve} />}
      {tier.steps && tier.steps.length > 0 && <StepChain steps={tier.steps} />}
      {tier.placements && tier.placements.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="text-sm text-muted-foreground">
            {`Where Meta ran it · share of delivery and fit vs ${tier.comparison ?? 'the baseline'}`}
          </span>
          <PlacementCards placements={tier.placements} format={format} />
        </div>
      )}
      {tier.audience && tier.audience.length > 0 && <AudienceLine audience={tier.audience} />}
      {evidence}
    </div>
  );
}

// A layer that needs work opens with everything it has. A layer that is fine, or cannot be
// read yet, is one line: its name, its status and its headline number. After "fix attention,
// keep the rest", a screen of proof per healthy layer is a screen between the reader and
// the next thing to do; it stays one click away.
function TierRow({ tier, broken, converting, format, evidence }: { tier: DiagnosisTier; broken: boolean; converting: boolean; format: Diagnosis['format']; evidence?: React.ReactNode }) {
  const open = tier.status === 'weak';
  const amber = broken && converting;
  const head = tier.metrics[0];
  return (
    <details open={open} className={cn('group/tier border-b border-border', broken && (converting ? 'bg-chart-2/[0.05] shadow-[inset_3px_0_0_var(--chart-2)]' : 'bg-destructive/[0.03] shadow-[inset_3px_0_0_var(--destructive)]'))}>
      <summary className="flex cursor-pointer list-none items-center gap-3 px-6 py-3.5 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
        {/* On an ad that converts cheaper than the account, the weak layer takes the verdict's
            amber rather than red, so the row does not contradict the verdict above it. */}
        <div className={cn('flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold tabular-nums', amber ? 'bg-chart-2 text-primary-foreground' : railClass[tier.status])}>{tier.tier}</div>
        <span className={cn('w-24 shrink-0 text-base font-semibold', tier.status === 'not_measured' && 'text-muted-foreground')} title={tier.question}>
          {tier.name}
        </span>
        <Pill tier={tier} className={amber ? 'bg-chart-2/15 text-chart-2' : undefined} />
        {head && head.value != null && (
          <span className="hidden min-w-0 truncate text-sm text-muted-foreground tabular-nums group-open/tier:hidden @xl:inline">
            {head.label} <span className="font-medium text-foreground">{fmtValue(head, head.value)}</span>
            {head.baseline != null && ` ${tier.key === 'fatigue' ? 'was' : 'vs'} ${fmtValue(head, head.baseline)}`}
            {head.lift != null && <span className={cn('ml-1.5 font-semibold', liftClass(tier, head))}>{signed(shownChange(head))}</span>}
          </span>
        )}
        <ChevronRight className="ml-auto size-4 shrink-0 text-muted-foreground transition-transform group-open/tier:rotate-90 motion-reduce:transition-none" aria-hidden="true" />
      </summary>
      <div className="px-6 pt-1 pb-5 @2xl:pl-[4.25rem]">
        <TierBody tier={tier} broken={broken} format={format} evidence={evidence} />
      </div>
    </details>
  );
}

const formulas: [string, string][] = [
  ['Hook rate', '3s views ÷ impressions'],
  ['Hold rate', 'ThruPlay ÷ 3s views'],
  ['Link CTR', 'link clicks ÷ impressions'],
  ['Frequency', 'impressions ÷ reach'],
  ['Engagement', 'reactions + comments + saves ÷ impressions'],
  ['Steps', 'each as a share of the previous one'],
];

// `evidence` is optional per-layer content from outside the diagnosis: what the tags say was
// in the ad at that layer, and account patterns the ad belongs to. The numbers stay the
// panel's own; evidence only ever sits under them.
// `afterVerdict` sits directly under the verdict: what to do about it belongs next to it.
export function DiagnosisPanel({
  diagnosis: d,
  evidence,
  afterVerdict,
  footnote,
}: {
  diagnosis: Diagnosis;
  evidence?: Partial<Record<DiagnosisTier['key'], React.ReactNode>>;
  afterVerdict?: React.ReactNode;
  footnote?: React.ReactNode;
}) {
  const v = d.verdict;
  // A weak layer on an ad that already converts cheaper than the account is a lead to iterate
  // on, not an alarm: amber, not red. Red stays for ads whose weak layer is costing money.
  const tone = v.kind === 'broken' ? (v.converting ? 'iterate' : 'broken') : v.kind;
  const Icon = { broken: AlertCircle, iterate: Lightbulb, healthy: CheckCircle2, too_early: Clock }[tone];
  const iconClass = { broken: 'text-destructive', iterate: 'text-chart-2', healthy: 'text-success', too_early: 'text-muted-foreground' }[tone];
  return (
    <section className="@container space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">Diagnosis (last 30 days)</h2>
        <span className="text-sm text-muted-foreground">{`Compared with ${d.baseline_creatives} other ${d.format}${d.baseline_creatives === 1 ? '' : 's'} in this account`}</span>
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div
          className={cn(
            'flex items-center gap-3.5 border-b border-border px-6 py-4',
            tone === 'broken' && 'bg-destructive/[0.06]',
            tone === 'iterate' && 'bg-chart-2/[0.08]',
            tone === 'healthy' && 'bg-success/[0.06]',
            tone === 'too_early' && 'bg-muted'
          )}
        >
          <Icon className={cn('size-5.5 shrink-0', iconClass)} aria-hidden="true" />
          <div className="flex flex-col gap-1">
            <span className="text-base leading-snug font-semibold">{v.headline}</span>
            {v.detail && <span className="text-sm leading-relaxed text-muted-foreground">{v.detail}</span>}
          </div>
        </div>
        {afterVerdict}
        {d.tiers.map((t) => (
          <TierRow key={t.key} tier={t} broken={t.tier === d.broken_tier} converting={!!v.converting} format={d.format} evidence={evidence?.[t.key]} />
        ))}
        <div className="flex flex-wrap gap-x-6 gap-y-1.5 bg-sidebar px-6 py-3.5 text-sm text-muted-foreground">
          {formulas.map(([name, def]) => (
            <span key={name}>
              <span className="font-medium text-foreground">{name}</span>
              {` = ${def}`}
            </span>
          ))}
          {footnote && <p className="w-full pt-1">{footnote}</p>}
        </div>
      </div>
    </section>
  );
}
