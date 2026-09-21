'use client';
import { useEffect, useRef, useState } from 'react';
import { seekTo } from '@/components/creative-detail-client';
import type { PlayCurve, PlayCurvePoint } from '@/lib/types';

// Share of plays still watching, second by second, beside other videos of similar length.
// Time runs on a square-root scale: the first three seconds, where most viewers leave, get a
// fifth of the width instead of a twentieth, and every tick is labelled so the stretch is
// visible. 0:00-0:03 is banded as the hook, the attention layer's window. Beats are faint
// markers; their names and timestamps are the Beats row under the chart, and hovering names
// the beat on screen at that second. Clicking plays the video from there.

const H = 168;
// The y-axis labels sit inside the plot, so its left edge lines up with the rows around it.
const PAD = { top: 8, bottom: 22, left: 0, right: 4 };
const HOOK_S = 3;
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
const pct = (x: number) => `${Math.round(x * 100)}%`;

// The finding in one line, from the gap to the peers at 0:03 (the hook) and the first second
// after it where the gap changes side. Under two points either way reads as level.
function takeaway(pts: PlayCurvePoint[]): string | null {
  const gap = (p: PlayCurvePoint) => (p.share != null && p.baseline_share != null ? p.share - p.baseline_share : null);
  const at3 = pts.find((p) => p.start_s === 3) ?? pts[Math.min(3, pts.length - 1)];
  const g3 = gap(at3);
  if (g3 == null) return null;
  const n = Math.round(Math.abs(g3) * 100);
  const head =
    n < 2 ? `Level with similar videos at ${clock(at3.start_s)}` : g3 < 0 ? `Loses ${n} points more than similar videos by ${clock(at3.start_s)}` : `Keeps ${n} points more than similar videos at ${clock(at3.start_s)}`;
  if (n < 2) return head;
  const flip = pts.find((p) => p.start_s > at3.start_s && (gap(p) ?? 0) * g3 < 0 && Math.abs(gap(p) ?? 0) >= 0.005);
  if (flip) return `${head}, then ${g3 < 0 ? 'holds better' : 'falls behind'} from ${clock(flip.start_s)}.`;
  return `${head}, and ${g3 < 0 ? 'stays behind' : 'stays ahead'} after.`;
}

export type Beat = { name: string; t: number };

export function PlayCurveChart({ curve, beats }: { curve: PlayCurve; beats: Beat[] }) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const pts = curve.points.filter((p) => p.share != null);
  if (pts.length < 2) return null;
  const dur = curve.duration_s;
  const tMax = dur != null && dur < 60 ? dur : 60;
  const past60 = dur == null || dur > 60;
  const x = (t: number) => PAD.left + Math.sqrt(Math.min(t, tMax) / tMax) * (w - PAD.left - PAD.right);
  const y = (s: number) => PAD.top + (1 - s) * (H - PAD.top - PAD.bottom);
  const path = (key: 'share' | 'baseline_share') =>
    pts
      .filter((p) => p[key] != null)
      .map((p, i) => `${i ? 'L' : 'M'}${x(p.start_s).toFixed(1)},${y(p[key] as number).toFixed(1)}`)
      .join(' ');
  // The line stays neutral: the shading carries better or worse, second by second.
  const stroke = 'var(--foreground)';
  const ticks = [0, 3, 5, 10, 15, 30, 60].filter((t) => t < tMax - 1 || t === tMax);
  if (!ticks.includes(tMax)) ticks.push(tMax);
  const tickLabel = (t: number) => (t === 60 && past60 ? '1:00+' : clock(t));

  // Beat markers after the hook; the hook itself is the band.
  const shownBeats = beats.filter((b) => b.t > 0 && b.t <= tMax).map((b) => ({ ...b, bx: x(b.t) }));
  const beatAt = (t: number) => [...beats].filter((b) => b.t <= t).sort((a, b) => b.t - a.t)[0];

  const nearest = (px: number) => pts.reduce((best, p, i) => (Math.abs(x(p.start_s) - px) < Math.abs(x(pts[best].start_s) - px) ? i : best), 0);
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => setHover(nearest(e.clientX - e.currentTarget.getBoundingClientRect().left));
  const h = hover != null ? pts[hover] : null;

  return (
    <div ref={box} className="relative w-full">
      <svg
        width={w}
        height={H}
        className="block cursor-pointer"
        role="img"
        aria-label="Share of plays still watching, second by second. Click to play the video from that second."
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        onClick={() => h && seekTo(h.start_s)}
      >
        <rect x={x(0)} y={PAD.top} width={x(Math.min(HOOK_S, tMax)) - x(0)} height={H - PAD.top - PAD.bottom} fill="var(--muted)" />
        <text x={x(0) + 4} y={H - PAD.bottom - 4} className="fill-muted-foreground text-[11px]">
          hook
        </text>
        {[0, 0.5, 1].map((s) => (
          <g key={s}>
            <line x1={PAD.left} x2={w - PAD.right} y1={y(s)} y2={y(s)} stroke="var(--border)" />
            {s > 0 && (
              <text x={w - PAD.right} y={y(s) + 12} textAnchor="end" className="fill-muted-foreground text-[11px] tabular-nums">
                {pct(s)}
              </text>
            )}
          </g>
        ))}
        {shownBeats.map((b, i) => (
          <line key={`${b.name}-${i}`} x1={b.bx} x2={b.bx} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--border)" strokeDasharray="2 3" />
        ))}
        {/* The gap to the peers, segment by segment: red where this ad has lost more viewers
            than they have by that second, green where it has kept more. */}
        {pts.slice(1).map((p, i) => {
          const a = pts[i];
          if (a.baseline_share == null || p.baseline_share == null) return null;
          const gap = (a.share as number) - a.baseline_share + ((p.share as number) - p.baseline_share);
          if (Math.abs(gap) < 0.01) return null;
          const poly = [
            [x(a.start_s), y(a.share as number)],
            [x(p.start_s), y(p.share as number)],
            [x(p.start_s), y(p.baseline_share)],
            [x(a.start_s), y(a.baseline_share)],
          ]
            .map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`)
            .join(' ');
          return <polygon key={p.start_s} points={poly} fill={gap < 0 ? 'var(--destructive)' : 'var(--success)'} fillOpacity={0.14} />;
        })}
        <path d={path('baseline_share')} fill="none" stroke="var(--muted-foreground)" strokeWidth={1.5} strokeDasharray="4 3" />
        <path d={path('share')} fill="none" stroke={stroke} strokeWidth={2.5} strokeLinejoin="round" />
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={H - 6} textAnchor={t === 0 ? 'start' : t === tMax ? 'end' : 'middle'} className="fill-muted-foreground text-[11px] tabular-nums">
            {tickLabel(t)}
          </text>
        ))}
        {h && (
          <g pointerEvents="none">
            <line x1={x(h.start_s)} x2={x(h.start_s)} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--foreground)" strokeOpacity={0.3} />
            <circle cx={x(h.start_s)} cy={y(h.share as number)} r={4} fill={stroke} />
          </g>
        )}
      </svg>
      <p className="mt-1 text-sm text-muted-foreground tabular-nums" aria-live="polite">
        {h
          ? `${clock(h.start_s)}${beatAt(h.start_s) ? ` · ${beatAt(h.start_s).name}` : ''}: ${pct(h.share as number)} still watching${h.baseline_share != null ? `, ${pct(h.baseline_share)} for ${curve.baseline_label ?? 'other videos'}` : ''}`
          : (takeaway(pts) ?? `Share of plays still watching${curve.baseline_label ? `; dashed: ${curve.baseline_label}` : ''}`)}
      </p>
    </div>
  );
}
