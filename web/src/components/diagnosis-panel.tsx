import { AlertCircle, CheckCircle2, Clock, Lock } from 'lucide-react';
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

function Pill({ tier }: { tier: DiagnosisTier }) {
  return (
    <span className={cn('inline-flex h-6 w-fit items-center gap-1.5 rounded-4xl px-2.5 text-sm font-medium', pillClass[tier.status])}>
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
        {m.lift != null && <span className={cn('text-sm font-semibold tabular-nums', liftClass(tier, m))}>{signed(m.lift)}</span>}
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

function TierRow({ tier, broken, last }: { tier: DiagnosisTier; broken: boolean; last: boolean }) {
  const weak = tier.status === 'weak';
  const text = weak ? tier.decision : tier.reason;
  return (
    <div
      className={cn(
        'grid gap-x-6 gap-y-3 px-6 py-5 @2xl:grid-cols-[180px_minmax(0,1fr)]',
        !last && 'border-b border-border',
        broken && 'bg-destructive/[0.03] shadow-[inset_3px_0_0_var(--destructive)]'
      )}
    >
      <div className="flex items-start gap-3">
        <div className={cn('flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold tabular-nums', railClass[tier.status])}>{tier.tier}</div>
        <div className="flex flex-col gap-2">
          <span className={cn('text-base leading-7 font-semibold', tier.status === 'not_measured' && 'text-muted-foreground')} title={tier.question}>
            {tier.name}
          </span>
          <Pill tier={tier} />
        </div>
      </div>
      <div className="flex min-w-0 flex-col gap-4">
        {text && <p className={cn('text-sm leading-relaxed', weak ? 'font-semibold' : 'text-muted-foreground')}>{text}</p>}
        {tier.metrics.length > 0 && (
          <div className="flex flex-wrap gap-x-10 gap-y-4">
            {tier.metrics.map((m) => (
              <MetricBlock key={m.key} tier={tier} m={m} />
            ))}
          </div>
        )}
        {tier.curve && <RetentionCurve curve={tier.curve} />}
        {tier.steps && tier.steps.length > 0 && <StepChain steps={tier.steps} />}
      </div>
    </div>
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

export function DiagnosisPanel({ diagnosis: d }: { diagnosis: Diagnosis }) {
  const v = d.verdict;
  const Icon = v.kind === 'broken' ? AlertCircle : v.kind === 'healthy' ? CheckCircle2 : Clock;
  return (
    <section className="@container space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">Diagnosis (last 30 days)</h2>
        <span className="text-sm text-muted-foreground">{`Compared with ${d.baseline_creatives} other ${d.format} creatives in this account`}</span>
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div
          className={cn(
            'flex items-center gap-3.5 border-b border-border px-6 py-4',
            v.kind === 'broken' && 'bg-destructive/[0.06]',
            v.kind === 'healthy' && 'bg-success/[0.06]',
            v.kind === 'too_early' && 'bg-muted'
          )}
        >
          <Icon className={cn('size-5.5 shrink-0', v.kind === 'broken' ? 'text-destructive' : v.kind === 'healthy' ? 'text-success' : 'text-muted-foreground')} aria-hidden="true" />
          <div className="flex flex-col gap-1">
            <span className="text-base leading-snug font-semibold">{v.headline}</span>
            <span className="text-sm leading-relaxed text-muted-foreground">{v.detail}</span>
          </div>
        </div>
        {d.tiers.map((t) => (
          <TierRow key={t.key} tier={t} broken={t.tier === d.broken_tier} last={false} />
        ))}
        <div className="flex flex-wrap gap-x-6 gap-y-1.5 bg-sidebar px-6 py-3.5 text-sm text-muted-foreground">
          {formulas.map(([name, def]) => (
            <span key={name}>
              <span className="font-medium text-foreground">{name}</span>
              {` = ${def}`}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}
