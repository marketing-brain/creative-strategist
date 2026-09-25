import { AlertCircle, CheckCircle2, ChevronRight, Clock, Lightbulb, Lock } from 'lucide-react';
import { cn } from 'cn';
import type { Diagnosis, DiagnosisCurve, DiagnosisMetric, DiagnosisStatus, DiagnosisStep, DiagnosisTier } from '@/lib/types';

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

const tickName = (p: number) => (p === 0 ? 'Start' : p === 100 ? 'End' : `${p}%`);

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;

// The line takes the layer's status colour: red only when retention is the weak layer.
const curveTone: Partial<Record<DiagnosisStatus, [string, string]>> = {
  weak: ['stroke-destructive', 'fill-destructive'],
  strong: ['stroke-success', 'fill-success'],
};

function RetentionCurve({ curve, status }: { curve: DiagnosisCurve; status: DiagnosisStatus }) {
  const [stroke, fill] = curveTone[status] ?? ['stroke-foreground', 'fill-foreground'];
  // Ticks in seconds when the length is known: "25%" of a 111s video hides that it is 0:28.
  const tick = (p: number) => (curve.duration_s != null ? clock((p / 100) * curve.duration_s) : tickName(p));
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
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block" role="img" aria-label="Watch-through curve">
        {segIdx && segIdx[0] >= 0 && segIdx[1] >= 0 && (
          <rect x={x(segIdx[0])} y={0} width={x(segIdx[1]) - x(segIdx[0])} height={bottom} className="fill-destructive/10" />
        )}
        <line x1="0" y1={bottom} x2={W} y2={bottom} className="stroke-border" strokeWidth="1" />
        <polyline fill="none" className="stroke-muted-foreground" strokeWidth="1.5" strokeDasharray="4 3" points={line('baseline_share')} />
        <polyline fill="none" className={stroke} strokeWidth="2.5" strokeLinejoin="round" points={line('share')} />
        {curve.points.map((p, i) => (
          <circle key={p.p} cx={x(i)} cy={y(p.share)} r="3.5" className={fill} />
        ))}
      </svg>
      <div className="grid tabular-nums" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
        {curve.points.map((p, i) => {
          const inDrop = seg != null && (p.p === seg[0] || p.p === seg[1]);
          const align = i === 0 ? 'text-left' : i === n - 1 ? 'text-right' : 'text-center';
          return (
            <div key={p.p} className={cn('flex flex-col gap-0.5', align)}>
              <span className="text-xs text-muted-foreground">{tick(p.p)}</span>
              <span className={cn('text-sm font-semibold', inDrop && 'text-destructive')}>{p.share == null ? '—' : pct(p.share)}</span>
            </div>
          );
        })}
      </div>
      {/* One caption: what the line is, what the dashes are, and the average watch. */}
      <span className="text-sm text-muted-foreground">
        {`Share of plays still watching${curve.baseline_label ? `; dashed: the ${curve.baseline_label}` : ''}`}
        {curve.avg_watch_s != null && ` · average watch ${clock(curve.avg_watch_s)}${curve.duration_s != null ? ` of ${clock(curve.duration_s)}` : ''}`}
      </span>
    </div>
  );
}

// The funnel as one row in the layers' label column: each step's rate, with similar videos in
// muted brackets. Steps the account does not track are named under it, because leaving them
// out silently would make "landing page → checkout" look like one step.
const FUNNEL = [
  ['landing_page', 'landing page'],
  ['content_view', 'content view'],
  ['add_to_cart', 'add to cart'],
  ['checkout', 'checkout'],
  ['purchase', 'purchase'],
] as const;

// The funnel drawn left to right: one bar per stage, its height the share of clickers still
// there, joined by bands that narrow as people leave. Heights are linear, so a cliff looks
// like one. Dashed lines on each bar mark the account's share at that stage (its other videos,
// pooled). Each band carries its step rate with the account's in brackets, coloured only when
// it clears the diagnosis's 15% bar; hover gives the counts.
const FW = 640;
const FH = 150;
const BAR = 64;
const TOP = 22; // room for the step rates above the bands
const BOTTOM = 22; // room for the stage names

function FunnelChart({ steps, name }: { steps: DiagnosisStep[]; name: (key: string) => string }) {
  // Cumulative share of clicks at each stage, for this ad and for similar videos; a stage whose
  // step cannot be read ends the chart there.
  const stages: { label: string; share: number; peer: number | null; step?: DiagnosisStep }[] = [{ label: 'click', share: 1, peer: 1 }];
  for (const st of steps) {
    const prev = stages[stages.length - 1];
    if (!st.readable || st.rate == null) break;
    stages.push({
      label: name(st.key),
      share: prev.share * st.rate,
      peer: prev.peer != null && st.baseline_rate != null ? prev.peer * st.baseline_rate : null,
      step: st,
    });
  }
  if (stages.length < 2) return null;
  const n = stages.length;
  const gap = (FW - n * BAR) / (n - 1);
  const x = (i: number) => i * (BAR + gap);
  const span = FH - TOP - BOTTOM;
  const mid = TOP + span / 2;
  const hOf = (share: number) => Math.max(2, share * span);
  return (
    <svg viewBox={`0 0 ${FW} ${FH}`} className="block h-auto w-full" role="img" aria-label="Funnel from click to purchase">
      {stages.slice(1).map((s, k) => {
        const i = k + 1;
        const a = stages[i - 1];
        const [ha, hb] = [hOf(a.share), hOf(s.share)];
        const [x0, x1] = [x(i - 1) + BAR, x(i)];
        const st = s.step as DiagnosisStep;
        return (
          <g key={s.label}>
            <polygon
              points={`${x0},${mid - ha / 2} ${x1},${mid - hb / 2} ${x1},${mid + hb / 2} ${x0},${mid + ha / 2}`}
              fill="var(--chart-1)"
              fillOpacity={0.12}
            >
              <title>{`${st.numerator.toLocaleString('en-US')} of ${st.denominator.toLocaleString('en-US')} ${a.label === 'click' ? 'clicks' : `at ${a.label}`} reached ${s.label}`}</title>
            </polygon>
            <text x={(x0 + x1) / 2} y={TOP - 8} textAnchor="middle" className="text-[13px] tabular-nums">
              {/* Coloured only when the step clears the diagnosis's 15% bar against the account. */}
              <tspan
                className={cn(
                  'font-semibold',
                  st.lift != null && st.lift >= 0.15 ? 'fill-success' : st.lift != null && st.lift <= -0.15 ? 'fill-destructive' : 'fill-foreground'
                )}
              >
                {pct(st.rate as number)}
              </tspan>
              {st.baseline_rate != null && <tspan className="fill-muted-foreground">{` (${pct(st.baseline_rate)})`}</tspan>}
            </text>
          </g>
        );
      })}
      {stages.map((s, i) => {
        const h = hOf(s.share);
        return (
          <g key={s.label}>
            <rect x={x(i)} y={mid - h / 2} width={BAR} height={h} rx={2} fill="var(--chart-1)" />
            {/* The account's share at this stage: dashed lines at its top and bottom edges. */}
            {s.peer != null &&
              i > 0 &&
              [mid - hOf(s.peer) / 2, mid + hOf(s.peer) / 2].map((yy, j) => (
                <line
                  key={j}
                  x1={x(i) - 6}
                  x2={x(i) + BAR + 6}
                  y1={yy}
                  y2={yy}
                  stroke="var(--foreground)"
                  strokeOpacity={0.55}
                  strokeWidth={1.5}
                  strokeDasharray="4 3"
                />
              ))}
            <text x={x(i) + BAR / 2} y={FH - 6} textAnchor="middle" className="fill-muted-foreground text-[13px]">
              {s.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function FunnelRow({ steps }: { steps: DiagnosisStep[] }) {
  const name = (key: string) => FUNNEL.find(([k]) => k === key)?.[1] ?? key.replaceAll('_', ' ');
  const present = new Set(steps.map((st) => st.key));
  const last = steps[steps.length - 1]?.key;
  const lastAt = FUNNEL.findIndex(([k]) => k === last);
  const untracked = FUNNEL.filter(([k], i) => i < lastAt && !present.has(k)).map(([, n]) => n);
  return (
    <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 text-sm">
      <span className="text-muted-foreground">Funnel</span>
      <div className="min-w-0 space-y-0.5">
        <FunnelChart steps={steps} name={name} />
        <p className="text-muted-foreground">
          {"( ) and dashed lines: the account's other videos"}
          {untracked.length > 0 && ` · ${untracked.join(' and ')} ${untracked.length > 1 ? "aren't" : "isn't"} tracked for this account`}
        </p>
      </div>
    </div>
  );
}

// A chart for one layer, drawn from outside the diagnosis, with its row label.
export type LayerChart = { label: string; node: React.ReactNode };

function TierBody({ tier, broken, evidence, chart }: { tier: DiagnosisTier; broken: boolean; evidence?: React.ReactNode; chart?: LayerChart }) {
  const weak = tier.status === 'weak';
  // The verdict above already carries the broken layer's decision word for word.
  // A healthy retention layer's sentence only restates its pill.
  // A healthy layer's sentence only restates its pill; a weak one carries the advice.
  const text = broken ? null : weak ? tier.decision : null;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {text && <p className={cn('max-w-prose text-sm leading-relaxed', weak ? 'font-semibold' : 'text-muted-foreground')}>{text}</p>}
      {/* Attention's hook rate and link CTR, and retention's hold rate, are already in the header. */}
      {/* No layer prints its figures as blocks: the header has the headline numbers, and what
          moved is in each layer's Hurting and Helping rows. A layer's chart sits in the same label
          column as its other rows; retention falls back to the quartile chart without one. */}
      {chart ? (
        <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 text-sm">
          <span className="text-muted-foreground">{chart.label}</span>
          <div className="min-w-0">{chart.node}</div>
        </div>
      ) : (
        tier.curve && <RetentionCurve curve={tier.curve} status={tier.status} />
      )}
      {tier.steps && tier.steps.length > 0 && <FunnelRow steps={tier.steps} />}
      {/* No audience line: under Advantage+ who Meta reached is the optimiser's choice, not a
          signal about the creative, and demographics are never a recommendation here. */}
      {evidence}
    </div>
  );
}

// A layer that needs work opens with everything it has. A layer that is fine, or cannot be
// read yet, is one line: its name, its status and its headline number. After "fix attention,
// keep the rest", a screen of proof per healthy layer is a screen between the reader and
// the next thing to do; it stays one click away.
function TierRow({
  tier,
  broken,
  converting,
  evidence,
  chart,
}: {
  tier: DiagnosisTier;
  broken: boolean;
  converting: boolean;
  evidence?: React.ReactNode;
  chart?: LayerChart;
}) {
  const open = tier.status === 'weak';
  const amber = broken && converting;
  const head = tier.metrics[0];
  return (
    <details
      open={open}
      className={cn(
        'group/tier border-b border-border',
        broken && (converting ? 'bg-chart-2/[0.05] shadow-[inset_3px_0_0_var(--chart-2)]' : 'bg-destructive/[0.03] shadow-[inset_3px_0_0_var(--destructive)]')
      )}
    >
      <summary className="flex cursor-pointer list-none items-center gap-3 px-6 py-3.5 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
        {/* On an ad that converts cheaper than the account, the weak layer takes the verdict's
            amber rather than red, so the row does not contradict the verdict above it. */}
        <div
          className={cn(
            'flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold tabular-nums',
            amber ? 'bg-chart-2 text-primary-foreground' : railClass[tier.status]
          )}
        >
          {tier.tier}
        </div>
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
        <ChevronRight
          className="ml-auto size-4 shrink-0 text-muted-foreground transition-transform group-open/tier:rotate-90 motion-reduce:transition-none"
          aria-hidden="true"
        />
      </summary>
      <div className="px-6 pt-1 pb-5 @2xl:pl-[4.25rem]">
        <TierBody tier={tier} broken={broken} evidence={evidence} chart={chart} />
      </div>
    </details>
  );
}

// `evidence` is optional per-layer content from outside the diagnosis: what the tags say was
// in the ad at that layer, and account patterns the ad belongs to. The numbers stay the
// panel's own; evidence only ever sits under them.
// `afterVerdict` sits directly under the verdict: what to do about it belongs next to it.
export function DiagnosisPanel({
  diagnosis: d,
  evidence,
  afterVerdict,
  footnote,
  charts,
  title = 'Diagnosis (last 30 days)',
  compare,
}: {
  diagnosis: Diagnosis;
  // Both default to the creative page's wording; the ad set read names its own window and peers.
  title?: string;
  compare?: string;
  evidence?: Partial<Record<DiagnosisTier['key'], React.ReactNode>>;
  afterVerdict?: React.ReactNode;
  footnote?: React.ReactNode;
  charts?: Partial<Record<DiagnosisTier['key'], LayerChart>>;
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
        <h2 className="text-sm font-semibold">{title}</h2>
        <span className="text-sm text-muted-foreground">
          {compare ?? `Compared with ${d.baseline_creatives} other ${d.format}${d.baseline_creatives === 1 ? '' : 's'} in this account`}
        </span>
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
        {/* Placement fit (the API's context layer) is left off the page: under Advantage+ Meta
            picks the placements, and the verdict already leaves it out. */}
        {d.tiers
          .filter((t) => t.key !== 'context')
          .map((t) => (
            <TierRow key={t.key} tier={t} broken={t.tier === d.broken_tier} converting={!!v.converting} evidence={evidence?.[t.key]} chart={charts?.[t.key]} />
          ))}
        {/* Metric definitions live on the header's labels (hover), where the numbers are. */}
        {footnote && <p className="bg-sidebar px-6 py-3 text-sm text-muted-foreground">{footnote}</p>}
      </div>
    </section>
  );
}
