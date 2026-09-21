'use client';
import { useEffect, useRef, useState } from 'react';
import { formatters } from '@/lib/format';
import type { DiagnosisTier, MetricDay } from '@/lib/types';

// Fatigue as it happens: the ad's response (hook rate for a video, link CTR otherwise) day by
// day over the last 45 days, as a 7-day rolling rate so one thin day does not read as a
// collapse, with the day's own rate as a faint dot. The two windows the fatigue verdict
// compares are banded: the 14 days before, and the last 7. Their figures in the caption are
// the diagnosis's own, so the chart and the verdict cannot disagree. Spend sits underneath as
// faint bars, because decay usually shows when delivery scales. The rate axis starts at zero:
// a steady ad should look steady.

const H = 150;
const PAD = { top: 18, bottom: 20, left: 0, right: 4 };
const SPAN = 45;
const DAY = 86_400_000;
const iso = (t: number) => new Date(t).toISOString().slice(0, 10);
const short = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });

type Point = { day: string; rate: number | null; rolling: number | null; spend: number };

export function FatigueChart({ days, tier, windowEnd, format, currency }: { days: MetricDay[]; tier: DiagnosisTier; windowEnd: string; format: string; currency?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const video = format === 'video';
  const name = video ? 'hook rate' : 'link CTR';
  const num = (d: MetricDay) => (video ? d.video_3s_views : d.link_clicks) ?? 0;
  const byDay = new Map(days.map((d) => [d.day, d]));
  const end = Date.parse(`${windowEnd}T00:00:00Z`);
  const at = (i: number) => iso(end - (SPAN - 1 - i) * DAY);
  const pts: Point[] = Array.from({ length: SPAN }, (_, i) => {
    const day = at(i);
    const d = byDay.get(day);
    let n = 0;
    let imp = 0;
    for (let k = 0; k < 7; k++) {
      const r = byDay.get(iso(Date.parse(`${day}T00:00:00Z`) - k * DAY));
      if (r) {
        n += num(r);
        imp += r.impressions;
      }
    }
    return { day, rate: d && d.impressions ? num(d) / d.impressions : null, rolling: imp ? n / imp : null, spend: d?.spend ?? 0 };
  });
  if (pts.filter((p) => p.rolling != null).length < 2) return null;

  const fmt = formatters(currency);
  const pct = (x: number) => fmt.pct(x, 1);
  const top = Math.max(...pts.map((p) => Math.max(p.rolling ?? 0, p.rate ?? 0))) * 1.15 || 1;
  const maxSpend = Math.max(...pts.map((p) => p.spend)) || 1;
  const plotW = w - PAD.left - PAD.right;
  const x = (i: number) => PAD.left + (i / (SPAN - 1)) * plotW;
  const y = (r: number) => PAD.top + (1 - r / top) * (H - PAD.top - PAD.bottom);
  const spendH = (H - PAD.top - PAD.bottom) * 0.3;
  const line = pts
    .map((p, i) => (p.rolling == null ? null : `${x(i).toFixed(1)},${y(p.rolling).toFixed(1)}`))
    .filter(Boolean)
    .map((c, i) => `${i ? 'L' : 'M'}${c}`)
    .join(' ');
  // The verdict's windows (diagnose_creative): the last 7 days to the window end, and the 14
  // before those.
  const band = (from: number, to: number) => ({ x0: x(from) - plotW / (SPAN - 1) / 2, x1: x(to) + plotW / (SPAN - 1) / 2 });
  const prior = band(SPAN - 21, SPAN - 8);
  const last = band(SPAN - 7, SPAN - 1);
  const m = tier.metrics.find((k) => k.key === (video ? 'hook_rate' : 'link_ctr'));
  const caption =
    m && m.value != null && m.baseline != null
      ? `${name.charAt(0).toUpperCase() + name.slice(1)} ${pct(m.value)} in the last 7 days, against ${pct(m.baseline)} in the 14 before.`
      : tier.reason;
  const h = hover != null ? pts[hover] : null;
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
    setHover(Math.max(0, Math.min(SPAN - 1, Math.round(((px - PAD.left) / plotW) * (SPAN - 1)))));
  };

  return (
    <div ref={box} className="w-full">
      <svg width={w} height={H} className="block" role="img" aria-label={`Daily ${name} over the last ${SPAN} days`} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {[prior, last].map((b, i) => (
          <g key={i}>
            {/* The two windows touch, so they take different shades and a divider between them. */}
            <rect x={b.x0} y={PAD.top} width={b.x1 - b.x0} height={H - PAD.top - PAD.bottom} fill="var(--muted)" fillOpacity={i === 0 ? 0.5 : 1} />
            {i === 1 && <line x1={b.x0} x2={b.x0} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--border)" />}
            <text x={(b.x0 + b.x1) / 2} y={PAD.top - 5} textAnchor="middle" className="fill-muted-foreground text-[11px]">
              {i === 0 ? '14 days before' : 'last 7 days'}
            </text>
          </g>
        ))}
        <line x1={PAD.left} x2={w - PAD.right} y1={y(0)} y2={y(0)} stroke="var(--border)" />
        {pts.map((p, i) => (
          <rect key={p.day} x={x(i) - 2} y={y(0) - (p.spend / maxSpend) * spendH} width={4} height={(p.spend / maxSpend) * spendH} fill="var(--muted-foreground)" fillOpacity={0.18} />
        ))}
        {pts.map((p, i) => (p.rate == null ? null : <circle key={p.day} cx={x(i)} cy={y(Math.min(p.rate, top))} r={1.8} fill="var(--muted-foreground)" fillOpacity={0.5} />))}
        <path d={line} fill="none" stroke="var(--foreground)" strokeWidth={2} strokeLinejoin="round" />
        {[0, Math.floor((SPAN - 1) / 2), SPAN - 1].map((i) => (
          <text key={i} x={x(i)} y={H - 5} textAnchor={i === 0 ? 'start' : i === SPAN - 1 ? 'end' : 'middle'} className="fill-muted-foreground text-[11px]">
            {short(pts[i].day)}
          </text>
        ))}
        {h && (
          <g pointerEvents="none">
            <line x1={x(hover as number)} x2={x(hover as number)} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--foreground)" strokeOpacity={0.3} />
            {h.rolling != null && <circle cx={x(hover as number)} cy={y(h.rolling)} r={3.5} fill="var(--foreground)" />}
          </g>
        )}
      </svg>
      <p className="mt-1 text-sm text-muted-foreground tabular-nums" aria-live="polite">
        {h
          ? `${short(h.day)}: ${name} ${h.rate != null ? pct(h.rate) : '–'} that day, ${h.rolling != null ? pct(h.rolling) : '–'} over the 7 days to it · ${fmt.money(h.spend)} spent`
          : caption}
      </p>
    </div>
  );
}
