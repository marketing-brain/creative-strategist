'use client';
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { DiagnosisTier, MetricDay } from '@/lib/types';

// Fatigue as it happens, drawn the way the Trend chart is: the ad's response (hook rate for a
// video, link CTR otherwise) as a 7-day rolling rate over the last 45 days, so one thin day
// does not read as a collapse, with daily spend as bars behind it, because decay usually
// shows when delivery scales. The two windows the fatigue verdict compares are shaded: the
// 14 days before, and the last 7. Their figures in the caption are the diagnosis's own, so
// the chart and the verdict cannot disagree.

const LINE_COLOR = '#3b82f6'; // the Trend chart's CTR blue
const SPAN = 45;
const DAY = 86_400_000;
const iso = (t: number) => new Date(t).toISOString().slice(0, 10);

type Row = { day: string; rolling: number | null; spend: number };

export function FatigueChart({ days, tier, windowEnd, format, currency = 'USD' }: { days: MetricDay[]; tier: DiagnosisTier; windowEnd: string; format: string; currency?: string }) {
  const video = format === 'video';
  const name = video ? 'Hook rate' : 'Link CTR';
  const num = (d: MetricDay) => (video ? d.video_3s_views : d.link_clicks) ?? 0;
  const byDay = new Map(days.map((d) => [d.day, d]));
  const end = Date.parse(`${windowEnd}T00:00:00Z`);
  const rows: Row[] = Array.from({ length: SPAN }, (_, i) => {
    const t = end - (SPAN - 1 - i) * DAY;
    const d = byDay.get(iso(t));
    let n = 0;
    let imp = 0;
    for (let k = 0; k < 7; k++) {
      const r = byDay.get(iso(t - k * DAY));
      if (r) {
        n += num(r);
        imp += r.impressions;
      }
    }
    return { day: iso(t), rolling: imp ? n / imp : null, spend: d?.spend ?? 0 };
  });
  if (rows.filter((r) => r.rolling != null).length < 2) return null;

  const dayFmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  let money: Intl.NumberFormat;
  try {
    money = new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 0 });
  } catch {
    money = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
  }
  const pct = new Intl.NumberFormat('en-US', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fmtDay = (d: string) => dayFmt.format(new Date(`${d}T00:00:00Z`));
  // The verdict's windows (diagnose_creative): the last 7 days to the window end, and the 14
  // before those.
  const at = (back: number) => rows[SPAN - 1 - back].day;
  const m = tier.metrics.find((k) => k.key === (video ? 'hook_rate' : 'link_ctr'));
  const caption =
    m && m.value != null && m.baseline != null ? `${name} ${pct.format(m.value)} in the last 7 days, against ${pct.format(m.baseline)} in the 14 before.` : tier.reason;

  return (
    <div className="w-full">
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={rows} margin={{ top: 18, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-border" vertical={false} />
          <XAxis dataKey="day" tickFormatter={fmtDay} minTickGap={32} tick={{ fontSize: 12 }} tickLine={false} axisLine={false} className="fill-muted-foreground" />
          <YAxis yAxisId="rate" tickFormatter={(v: number) => pct.format(v)} tick={{ fontSize: 12 }} tickLine={false} axisLine={false} width={48} className="fill-muted-foreground" />
          <YAxis yAxisId="spend" hide domain={[0, (max: number) => max * 2.5]} />
          <ReferenceArea yAxisId="rate" x1={at(20)} x2={at(7)} fill="currentColor" fillOpacity={0.04} className="text-foreground" ifOverflow="visible" label={{ value: '14 days before', position: 'top', fontSize: 11, fill: 'var(--muted-foreground)' }} />
          <ReferenceArea yAxisId="rate" x1={at(6)} x2={at(0)} fill="currentColor" fillOpacity={0.1} className="text-foreground" ifOverflow="visible" label={{ value: 'last 7 days', position: 'top', fontSize: 11, fill: 'var(--muted-foreground)' }} />
          <Tooltip
            labelFormatter={(l) => fmtDay(String(l))}
            formatter={(v, key) => [v == null ? '–' : key === 'Spend' ? money.format(Number(v)) : pct.format(Number(v)), key]}
            contentStyle={{ background: 'var(--popover)', color: 'var(--popover-foreground)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: 'var(--popover-foreground)' }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar yAxisId="spend" dataKey="spend" name="Spend" fill="currentColor" className="text-muted-foreground/30" isAnimationActive={false} />
          <Line yAxisId="rate" type="monotone" dataKey="rolling" stroke={LINE_COLOR} strokeWidth={2} dot={false} name={`${name}, 7-day`} isAnimationActive={false} connectNulls />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="mt-1 text-sm text-muted-foreground tabular-nums">{caption}</p>
    </div>
  );
}
