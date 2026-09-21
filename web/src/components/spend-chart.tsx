'use client';
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MetricDay } from '@/lib/types';

// How Meta has been spending on the ad, and what each purchase has cost as it did: daily spend
// as bars, cost per purchase as a 7-day rolling line (spend over purchases for the 7 days to
// each day). A single day's cost swings with a handful of orders, so the daily figure would
// draw noise as a trend. The 30 days the header and the diagnosis read are shaded. Drawn like
// the Fatigue chart, on one axis: the spend bars are scaled to sit behind the line.

const LINE_COLOR = '#f59e0b';
const DAY = 86_400_000;
const WEEK_PURCHASES_FLOOR = 30; // fewer purchases in a week and the rolling cost is still noise
const BAR = 0.15; // the diagnosis's bar for calling a change real
const iso = (t: number) => new Date(t).toISOString().slice(0, 10);

type Row = { day: string; spend: number; cpa: number | null; week: { spend: number; purchases: number } };

export function SpendChart({ days, windowStart, windowEnd, currency = 'USD' }: { days: MetricDay[]; windowStart: string; windowEnd: string; currency?: string }) {
  const byDay = new Map(days.map((d) => [d.day, d]));
  const sorted = [...days].map((d) => d.day).sort();
  if (!sorted.length) return null;
  const first = Date.parse(`${sorted[0]}T00:00:00Z`);
  const last = Date.parse(`${windowEnd}T00:00:00Z`);
  const rows: Row[] = [];
  for (let t = first; t <= last; t += DAY) {
    let spend = 0;
    let purchases = 0;
    for (let k = 0; k < 7; k++) {
      const r = byDay.get(iso(t - k * DAY));
      if (r) {
        spend += r.spend;
        purchases += r.conversions;
      }
    }
    rows.push({ day: iso(t), spend: byDay.get(iso(t))?.spend ?? 0, cpa: purchases ? spend / purchases : null, week: { spend, purchases } });
  }
  if (rows.filter((r) => r.cpa != null).length < 2) return null;

  const dayFmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  let money: Intl.NumberFormat;
  try {
    money = new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 0 });
  } catch {
    money = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
  }
  const fmtDay = (d: string) => dayFmt.format(new Date(`${d}T00:00:00Z`));

  // The caption compares the first full week inside the 30-day window with its last week, the
  // same 7-day figures the line draws. It only calls a direction past the 15% bar, and says so
  // when a week has too few purchases to read.
  const weekIn = rows.find((r) => r.day === iso(Date.parse(`${windowStart}T00:00:00Z`) + 6 * DAY));
  const weekEnd = rows[rows.length - 1];
  let caption: string | null = null;
  if (weekIn && weekEnd && weekIn.cpa != null && weekEnd.cpa != null) {
    if (weekIn.week.purchases < WEEK_PURCHASES_FLOOR || weekEnd.week.purchases < WEEK_PURCHASES_FLOOR) {
      caption = 'Too few purchases a week to read a trend in cost per purchase.';
    } else {
      const change = weekEnd.cpa / weekIn.cpa - 1;
      const verb = Math.abs(change) < BAR ? 'held' : change > 0 ? 'rose' : 'fell';
      caption = `Over the last 30 days, cost per purchase ${verb}: ${money.format(weekIn.cpa)} in the first week, ${money.format(weekEnd.cpa)} in the last, while daily spend went from ${money.format(weekIn.week.spend / 7)} to ${money.format(weekEnd.week.spend / 7)}.`;
    }
  }
  const from = rows.find((r) => r.day >= windowStart)?.day;

  return (
    <div className="w-full">
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={rows} margin={{ top: 18, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-border" vertical={false} />
          <XAxis dataKey="day" tickFormatter={fmtDay} minTickGap={32} tick={{ fontSize: 12 }} tickLine={false} axisLine={false} className="fill-muted-foreground" />
          <YAxis yAxisId="cpa" tickFormatter={(v: number) => money.format(v)} tick={{ fontSize: 12 }} tickLine={false} axisLine={false} width={48} className="fill-muted-foreground" />
          <YAxis yAxisId="spend" hide domain={[0, (max: number) => max * 2.5]} />
          {from && (
            <ReferenceArea yAxisId="cpa" x1={from} x2={windowEnd} fill="currentColor" fillOpacity={0.06} className="text-foreground" ifOverflow="visible" label={{ value: 'last 30 days', position: 'top', fontSize: 11, fill: 'var(--muted-foreground)' }} />
          )}
          <Tooltip
            labelFormatter={(l) => fmtDay(String(l))}
            formatter={(v, key) => [v == null ? '–' : money.format(Number(v)), key]}
            contentStyle={{ background: 'var(--popover)', color: 'var(--popover-foreground)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: 'var(--popover-foreground)' }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar yAxisId="spend" dataKey="spend" name="Spend" fill="currentColor" className="text-muted-foreground/30" isAnimationActive={false} />
          <Line yAxisId="cpa" type="monotone" dataKey="cpa" stroke={LINE_COLOR} strokeWidth={2} dot={false} name="Cost per purchase, 7-day" isAnimationActive={false} connectNulls />
        </ComposedChart>
      </ResponsiveContainer>
      {caption && <p className="mt-1 text-sm text-muted-foreground tabular-nums">{caption}</p>}
    </div>
  );
}
