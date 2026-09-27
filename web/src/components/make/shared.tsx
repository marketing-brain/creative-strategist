'use client';

import { useState } from 'react';
import { Play } from 'lucide-react';
import { cn } from 'cn';
import type { MakeReason } from '@/lib/types';
import { clock, pct } from '@/lib/make-format';

export { clock, clock1, money, money2, parseClock, pct, shortTitle, signedPct } from '@/lib/make-format';

// Make follows the house type system (diagnosis-panel.tsx): 24px figures, 16px names, 14px
// everything else, 12px only for chart ticks; hierarchy from weight; muted text only at 14px
// and up. Colour is reserved for status: the reason a video is here, the drop-off on the
// curve, and cost (free / paid), using the same tokens /next uses for its verbs.

const REASON: Record<MakeReason, { label: string; chip: string }> = {
  winner: { label: 'Winner', chip: 'border-success/40 text-success' },
  winner_worn: { label: 'Winner, wearing out', chip: 'border-chart-2/50 text-chart-2' },
  weak_opening: { label: 'Weak opening', chip: 'border-destructive/40 text-destructive' },
};

export function ReasonChips({ reasons, className }: { reasons: MakeReason[]; className?: string }) {
  if (!reasons.length) return null;
  return (
    <ul className={cn('flex flex-wrap gap-1.5', className)}>
      {reasons.map((r) => (
        <li key={r} className={cn('rounded-sm border px-1.5 py-0.5 text-sm leading-none font-medium whitespace-nowrap', REASON[r].chip)}>
          {REASON[r].label}
        </li>
      ))}
    </ul>
  );
}

export function Poster({ url, className }: { url: string | null; className?: string }) {
  // 4:5 matches the Meta feed frame and the /next posters.
  return (
    <div className={cn('relative shrink-0 overflow-hidden rounded-lg bg-muted aspect-[4/5]', className)}>
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover" />
      ) : (
        <Play className="absolute inset-0 m-auto size-5 text-muted-foreground/40" aria-hidden="true" />
      )}
    </div>
  );
}

export function StatBand({ cells }: { cells: { label: string; value: string; sub?: string; tone?: 'bad' | 'good' }[] }) {
  return (
    <dl className={cn('grid gap-px overflow-hidden rounded-xl border border-border bg-border', cells.length >= 4 ? 'grid-cols-2 lg:grid-cols-4' : 'grid-cols-2 sm:grid-cols-3')}>
      {cells.map((c) => (
        <div key={c.label} className="flex flex-col gap-1.5 bg-card p-4">
          <dt className="text-sm text-muted-foreground">{c.label}</dt>
          <dd className="text-2xl leading-none font-semibold tabular-nums">{c.value}</dd>
          {c.sub && (
            <dd className={cn('text-sm tabular-nums', c.tone === 'bad' ? 'text-destructive' : c.tone === 'good' ? 'text-success' : 'text-muted-foreground')}>
              {c.sub}
            </dd>
          )}
        </div>
      ))}
    </dl>
  );
}

/** Share of viewers still watching, log scale, with the steepest local drop shaded. */
export function WatchCurve({
  curve,
  duration,
  drop,
}: {
  curve: [number, number][];
  duration: number;
  drop: { start: number; end: number } | null;
}) {
  const [hover, setHover] = useState<[number, number] | null>(null);
  const pts = curve.filter(([t, v]) => v > 0 && t <= duration);
  if (pts.length < 3) {
    return <p className="text-sm text-muted-foreground">No watch curve for this video yet.</p>;
  }
  const W = 640, H = 200, L = 44, R = 14, T = 10, B = 26;
  const X1 = Math.max(duration, pts[pts.length - 1][0]);
  const ymin = Math.min(0.015, ...pts.map((p) => p[1]));
  const x = (s: number) => L + (s / X1) * (W - L - R);
  const y = (v: number) => T + (Math.log(1 / v) / Math.log(1 / ymin)) * (H - T - B);
  const ticks = [1, 0.3, 0.1, 0.03].filter((v) => v >= ymin);
  const step = X1 > 60 ? 15 : X1 > 30 ? 10 : 5;
  const xt = Array.from({ length: Math.floor(X1 / step) + 1 }, (_, i) => i * step);
  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const sx = ((e.clientX - r.left) / r.width) * W;
    setHover(pts.reduce((b, p) => (Math.abs(x(p[0]) - sx) < Math.abs(x(b[0]) - sx) ? p : b), pts[0]));
  };
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img" aria-label="Share of viewers still watching, by second">
        {drop && <rect x={x(drop.start)} y={T} width={Math.max(2, x(drop.end) - x(drop.start))} height={H - T - B} className="fill-destructive/10" />}
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="stroke-border" strokeWidth={1} />
            <text x={L - 8} y={y(v) + 4} textAnchor="end" className="fill-muted-foreground text-[12px]">{pct(v)}</text>
          </g>
        ))}
        {xt.map((s) => (
          <text key={s} x={x(s)} y={H - 6} textAnchor="middle" className="fill-muted-foreground text-[12px]">{clock(s)}</text>
        ))}
        <path
          d={pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(' ')}
          fill="none"
          className="stroke-primary"
          strokeWidth={2}
          strokeLinejoin="round"
        />
        {hover && <circle cx={x(hover[0])} cy={y(hover[1])} r={4.5} className="fill-primary stroke-card" strokeWidth={2} />}
        <rect x={L} y={T} width={W - L - R} height={H - T - B} fill="transparent" onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      {hover && (
        <div
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-[130%] rounded-md bg-foreground px-2 py-1 text-sm whitespace-nowrap text-background tabular-nums"
          style={{ left: `${(x(hover[0]) / W) * 100}%`, top: `${(y(hover[1]) / H) * 100}%` }}
        >
          {clock(hover[0])} · {pct(hover[1])} still watching
        </div>
      )}
    </div>
  );
}

/** The heading for one group of variants, with what it costs. */
export function GroupHeader({ title, cost, tone, children }: { title: string; cost: string; tone: 'free' | 'paid' | 'shoot'; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h3 className="text-base font-semibold">{title}</h3>
      <span
        className={cn(
          'rounded-sm border px-1.5 py-0.5 text-sm leading-none font-medium',
          tone === 'free' ? 'border-success/40 text-success' : tone === 'paid' ? 'border-chart-2/50 text-chart-2' : 'border-foreground/25 text-foreground',
        )}
      >
        {cost}
      </span>
      {children && <p className="w-full max-w-prose text-sm text-muted-foreground">{children}</p>}
    </div>
  );
}
