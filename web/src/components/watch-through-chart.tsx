'use client';

import { useEffect, useRef, useState } from 'react';
import type { AdSetRow } from '@/lib/types';

// Share of plays still watching at each point of the video, for every video in one ad set,
// on this ad set's delivery only. Focus and context: the best file is the one accent line,
// every sibling a thin neutral line; hovering any line names it and lifts it. Points are
// fractions of each video's own length, so a 9-second and a 90-second video share an axis.

const H = 176;
const PAD = { top: 10, bottom: 24, left: 34, right: 12 };
const STOPS = ['Start', '25%', '50%', '75%', '95%', '100%'];
const pct = (x: number) => `${Math.round(x * 100)}%`;

export function WatchThroughChart({ rows, bestId }: { rows: AdSetRow[]; bestId: string | null }) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(560);
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const lines = rows.filter((r) => r.retention && r.retention.length).map((r) => ({ row: r, pts: [1, ...r.retention!.map((s) => s.share)] }));
  if (lines.length < 2) return null;
  const x = (i: number) => PAD.left + (i / (STOPS.length - 1)) * (w - PAD.left - PAD.right);
  const y = (s: number) => PAD.top + (1 - Math.min(1, s)) * (H - PAD.top - PAD.bottom);
  const d = (pts: number[]) => pts.map((s, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(s).toFixed(1)}`).join(' ');
  // Draw order: siblings, then the hovered line, then the best on top.
  const order = [...lines].sort((a, b) => rank(a.row.id) - rank(b.row.id));
  function rank(id: string) {
    return id === bestId ? 2 : id === hover ? 1 : 0;
  }
  const focus = lines.find((l) => l.row.id === (hover ?? bestId));

  return (
    <figure className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {bestId && (
          <span className="inline-flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded-full bg-chart-1" aria-hidden="true" />
            Best file
          </span>
        )}
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded-full bg-chart-5/60" aria-hidden="true" />
          Other videos in this ad set
        </span>
        <span className="ml-auto min-h-4 truncate text-foreground" aria-live="polite">
          {focus
            ? `${focus.row.name ?? 'Untitled'} · ${focus.pts
                .slice(1)
                .map((s, i) => `${STOPS[i + 1]} ${pct(s)}`)
                .join(' · ')}`
            : ''}
        </span>
      </div>
      <div ref={box} className="w-full" onMouseLeave={() => setHover(null)}>
        <svg width={w} height={H} role="img" aria-label="Share of plays still watching at each point of each video in this ad set">
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={w - PAD.right} y1={y(t)} y2={y(t)} className="stroke-border" strokeWidth={1} />
              <text x={PAD.left - 6} y={y(t) + 3} textAnchor="end" className="fill-muted-foreground text-[10px] tabular-nums">
                {pct(t)}
              </text>
            </g>
          ))}
          {STOPS.map((s, i) => (
            <text key={s} x={x(i)} y={H - 6} textAnchor="middle" className="fill-muted-foreground text-[10px]">
              {s}
            </text>
          ))}
          {order.map(({ row, pts }) => {
            const best = row.id === bestId;
            const lit = row.id === hover;
            return (
              <g key={row.id}>
                <path
                  d={d(pts)}
                  fill="none"
                  strokeWidth={best || lit ? 2 : 1.25}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  className={best ? 'stroke-chart-1' : lit ? 'stroke-foreground' : 'stroke-chart-5/50'}
                />
                {/* A wide invisible stroke is the hover target, so a 1px line is easy to catch. */}
                <path d={d(pts)} fill="none" stroke="transparent" strokeWidth={12} onMouseEnter={() => setHover(row.id)} />
                {(best || lit) &&
                  pts.map((s, i) => (
                    <circle key={i} cx={x(i)} cy={y(s)} r={3} className={best ? 'fill-chart-1 stroke-card' : 'fill-foreground stroke-card'} strokeWidth={2} />
                  ))}
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption className="text-xs text-muted-foreground">
        Share of plays still watching at each point of the video, counted on this ad set&apos;s delivery only. Hover a line to name it.
      </figcaption>
    </figure>
  );
}
