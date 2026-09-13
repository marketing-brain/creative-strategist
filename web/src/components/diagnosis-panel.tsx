import { AlertCircle, CheckCircle2, Clock, Lock } from 'lucide-react';
import { cn } from 'cn';
import type { Diagnosis, DiagnosisCurve, DiagnosisMetric, DiagnosisStatus, DiagnosisStep, DiagnosisTier } from '@/lib/types';

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
    <span className={cn('inline-flex h-5 w-fit items-center gap-1 rounded-4xl px-2 text-xs font-medium', pillClass[tier.status])}>
      {tier.status === 'not_measured' && <Lock className="size-3" aria-hidden="true" />}
      {statusLabel(tier)}
    </span>
  );
}

function MetricBlock({ tier, m }: { tier: DiagnosisTier; m: DiagnosisMetric }) {
  const compare = tier.key === 'fatigue' ? 'was' : 'vs';
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{m.label}</span>
      <div className="flex items-baseline gap-2">
        <span className="text-[22px] leading-none font-semibold tabular-nums">{fmtValue(m, m.value)}</span>
        {m.baseline != null && <span className="text-xs text-muted-foreground tabular-nums">{`${compare} ${fmtValue(m, m.baseline)}`}</span>}
        {m.lift != null && <span className={cn('text-xs font-semibold tabular-nums', liftClass(tier, m))}>{signed(m.lift)}</span>}
      </div>
      <span className="text-[11px] text-muted-foreground">{m.note}</span>
    </div>
  );
}

function RetentionCurve({ curve }: { curve: DiagnosisCurve }) {
  const W = 320;
  const H = 72;
  const top = 4;
  const bottom = 66;
  const n = curve.points.length;
  const x = (i: number) => (n > 1 ? (i / (n - 1)) * W : 0);
  const y = (s: number | null) => (s == null ? bottom : bottom - s * (bottom - top));
  const line = (key: 'share' | 'baseline_share') => curve.points.map((p, i) => `${x(i)},${y(p[key])}`).join(' ');
  const seg = curve.drop_segment;
  const segIdx = seg ? [curve.points.findIndex((p) => p.p === seg[0]), curve.points.findIndex((p) => p.p === seg[1])] : null;
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <span className="text-xs text-muted-foreground">Watch-through curve · share of plays reaching each point</span>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block" role="img" aria-label="Watch-through curve">
        <line x1="0" y1={bottom} x2={W} y2={bottom} className="stroke-border" strokeWidth="1" />
        {segIdx && segIdx[0] >= 0 && segIdx[1] >= 0 && (
          <rect x={x(segIdx[0])} y={top - 2} width={x(segIdx[1]) - x(segIdx[0])} height={bottom - top + 2} className="fill-destructive/10" />
        )}
        <polyline fill="none" className="stroke-muted-foreground" strokeWidth="1.5" strokeDasharray="4 3" points={line('baseline_share')} />
        <polyline fill="none" className="stroke-destructive" strokeWidth="2.5" strokeLinejoin="round" points={line('share')} />
        {curve.points.map((p, i) => (
          <circle key={p.p} cx={x(i)} cy={y(p.share)} r="3" className="fill-destructive" />
        ))}
      </svg>
      <div className="grid text-[11px] text-muted-foreground tabular-nums" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
        {curve.points.map((p, i) => {
          const inDrop = seg != null && (p.p === seg[0] || p.p === seg[1]);
          return (
            <span key={p.p} className={cn(i === 0 ? 'text-left' : i === n - 1 ? 'text-right' : 'text-center', inDrop && 'font-semibold text-destructive')}>
              {`p${p.p} · ${p.share == null ? '—' : pct(p.share)}`}
            </span>
          );
        })}
      </div>
      <span className="text-[11px] text-muted-foreground">
        Dashed line is the account baseline.
        {curve.avg_watch_s != null && ` Avg watch time ${curve.avg_watch_s.toFixed(1)}s${curve.duration_s != null ? ` of ${Math.round(curve.duration_s)}s` : ''}.`}
      </span>
    </div>
  );
}

function StepChain({ steps }: { steps: DiagnosisStep[] }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-stretch gap-1.5">
        {steps.map((s) => (
          <div
            key={s.key}
            className={cn('flex min-w-[6.5rem] flex-1 flex-col gap-0.5 rounded-lg border px-2.5 py-2', s.readable ? 'border-border' : 'border-dashed border-muted-foreground/40 text-muted-foreground')}
          >
            <span className="text-[11px] text-muted-foreground">{s.label}</span>
            {s.readable && s.rate != null ? (
              <div className="flex items-baseline gap-1.5">
                <span className="text-base font-semibold tabular-nums">{pct(s.rate)}</span>
                {s.baseline_rate != null && <span className="text-[11px] text-muted-foreground tabular-nums">{`vs ${pct(s.baseline_rate)}`}</span>}
              </div>
            ) : (
              <span className="text-xs tabular-nums">{s.numerator > 0 ? `${s.numerator} · below floor` : '—'}</span>
            )}
          </div>
        ))}
      </div>
      <span className="text-[11px] text-muted-foreground">Each step as a share of the previous one.</span>
    </div>
  );
}

function TierRow({ tier, broken, last }: { tier: DiagnosisTier; broken: boolean; last: boolean }) {
  const evidence = tier.status === 'weak' ? tier.decision : tier.reason;
  const hasFigures = tier.metrics.length > 0 || tier.curve != null || tier.steps != null;
  return (
    <div
      className={cn(
        'grid gap-5 px-5 py-4 lg:grid-cols-[200px_minmax(0,1fr)_260px]',
        !last && 'border-b border-border',
        broken && 'bg-destructive/[0.03] shadow-[inset_3px_0_0_var(--destructive)]'
      )}
    >
      <div className="flex items-start gap-3">
        <div className="flex shrink-0 flex-col items-center gap-1">
          <div className={cn('flex size-6 items-center justify-center rounded-full text-xs font-semibold tabular-nums', railClass[tier.status])}>{tier.tier}</div>
          {!last && <div className="h-10 w-px bg-border" />}
        </div>
        <div className="flex flex-col gap-1.5">
          <span className={cn('text-sm font-semibold', tier.status === 'not_measured' && 'text-muted-foreground')}>{tier.name}</span>
          <Pill tier={tier} />
          <span className="text-xs text-muted-foreground">{tier.question}</span>
        </div>
      </div>
      <div className="flex min-w-0 flex-wrap gap-7">
        {tier.metrics.map((m) => (
          <MetricBlock key={m.key} tier={tier} m={m} />
        ))}
        {tier.curve && <RetentionCurve curve={tier.curve} />}
        {tier.steps && <StepChain steps={tier.steps} />}
        {!hasFigures && evidence && <p className="text-[13px] leading-snug text-muted-foreground">{evidence}</p>}
      </div>
      <div className={cn('text-[13px] leading-snug', tier.status === 'weak' ? 'font-semibold' : 'text-muted-foreground')}>{hasFigures && evidence}</div>
    </div>
  );
}

export function DiagnosisPanel({ diagnosis: d }: { diagnosis: Diagnosis }) {
  const v = d.verdict;
  const Icon = v.kind === 'broken' ? AlertCircle : v.kind === 'healthy' ? CheckCircle2 : Clock;
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-sm font-semibold">Diagnosis (last 30 days)</h2>
        <span className="text-xs text-muted-foreground">{`vs ${d.baseline_creatives} other ${d.format} creatives in this account`}</span>
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div
          className={cn(
            'flex items-center gap-3 border-b border-border px-5 py-3.5',
            v.kind === 'broken' && 'bg-destructive/[0.06]',
            v.kind === 'healthy' && 'bg-success/[0.06]',
            v.kind === 'too_early' && 'bg-muted'
          )}
        >
          <Icon className={cn('size-[18px] shrink-0', v.kind === 'broken' ? 'text-destructive' : v.kind === 'healthy' ? 'text-success' : 'text-muted-foreground')} aria-hidden="true" />
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-semibold">{v.headline}</span>
            <span className="text-[13px] text-muted-foreground">{v.detail}</span>
          </div>
        </div>
        {d.tiers.map((t, i) => (
          <TierRow key={t.key} tier={t} broken={t.tier === d.broken_tier} last={i === d.tiers.length - 1} />
        ))}
      </div>
    </section>
  );
}
