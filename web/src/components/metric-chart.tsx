'use client';
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MetricDay } from '@/lib/types';

const CTR_COLOR = '#3b82f6';
const CPA_COLOR = '#f59e0b';

// Spend sits behind the two lines because a buyer reads cost against scale: a cost that rises
// as spend rises is a different story from one that rises on flat spend. The shaded band is
// the window the diagnosis above was read from, so the two can be squared by eye.
export function MetricChart({ rows, currency = 'USD', windowStart, windowEnd }: { rows: MetricDay[]; currency?: string; windowStart?: string; windowEnd?: string }) {
  if (rows.length === 0) {
    return (
      <div className="flex h-[260px] items-center justify-center rounded-xl border border-dashed border-border text-sm text-muted-foreground">
        No metrics yet.
      </div>
    );
  }
  const day = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  let money: Intl.NumberFormat;
  try {
    money = new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 0 });
  } catch {
    money = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
  }
  const pct = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 1 });
  const fmtDay = (iso: string) => day.format(new Date(`${iso}T00:00:00Z`));
  // The band needs endpoints that exist on the axis; clamp the window to the days plotted.
  const days = rows.map((r) => r.day);
  const from = windowStart && days.find((d) => d >= windowStart);
  const to = windowEnd && [...days].reverse().find((d) => d <= windowEnd);
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart data={rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-border" vertical={false} />
        <XAxis dataKey="day" tickFormatter={fmtDay} minTickGap={32} tick={{ fontSize: 12 }} tickLine={false} axisLine={false} className="fill-muted-foreground" />
        <YAxis yAxisId="ctr" tickFormatter={(v: number) => pct.format(v)} tick={{ fontSize: 12 }} tickLine={false} axisLine={false} width={44} className="fill-muted-foreground" />
        <YAxis yAxisId="cpa" orientation="right" tickFormatter={(v: number) => money.format(v)} tick={{ fontSize: 12 }} tickLine={false} axisLine={false} width={48} className="fill-muted-foreground" />
        <YAxis yAxisId="spend" hide domain={[0, (max: number) => max * 2.5]} />
        {from && to && <ReferenceArea yAxisId="ctr" x1={from} x2={to} fill="currentColor" fillOpacity={0.06} className="text-foreground" ifOverflow="visible" />}
        <Tooltip
          labelFormatter={(l) => fmtDay(String(l))}
          formatter={(v, name) => [v == null ? '–' : name === 'CTR' ? pct.format(Number(v)) : money.format(Number(v)), name]}
          contentStyle={{ background: 'var(--popover)', color: 'var(--popover-foreground)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: 'var(--popover-foreground)' }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar yAxisId="spend" dataKey="spend" name="Spend" fill="currentColor" className="text-muted-foreground/30" isAnimationActive={false} />
        <Line yAxisId="ctr" type="monotone" dataKey="ctr" stroke={CTR_COLOR} strokeWidth={2} dot={false} name="CTR" isAnimationActive={false} />
        <Line yAxisId="cpa" type="monotone" dataKey="cpa" stroke={CPA_COLOR} strokeWidth={2} dot={false} name="Cost per purchase" isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
