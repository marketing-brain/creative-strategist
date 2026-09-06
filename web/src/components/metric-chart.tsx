'use client';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MetricDay } from '@/lib/types';

const CTR_COLOR = '#3b82f6';
const CPA_COLOR = '#f59e0b';

export function MetricChart({ rows }: { rows: MetricDay[] }) {
  if (rows.length === 0) {
    return (
      <div className="flex h-[240px] items-center justify-center rounded-xl border border-dashed border-border text-sm text-muted-foreground">
        No metrics yet.
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-border" vertical={false} />
        <XAxis dataKey="day" tick={{ fontSize: 12 }} tickLine={false} axisLine={false} className="fill-muted-foreground" />
        <YAxis yAxisId="l" tick={{ fontSize: 12 }} tickLine={false} axisLine={false} width={40} className="fill-muted-foreground" />
        <YAxis yAxisId="r" orientation="right" tick={{ fontSize: 12 }} tickLine={false} axisLine={false} width={40} className="fill-muted-foreground" />
        <Tooltip
          contentStyle={{ background: 'var(--popover)', color: 'var(--popover-foreground)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: 'var(--popover-foreground)' }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line yAxisId="l" type="monotone" dataKey="ctr" stroke={CTR_COLOR} strokeWidth={2} dot={false} name="CTR" />
        <Line yAxisId="r" type="monotone" dataKey="cpa" stroke={CPA_COLOR} strokeWidth={2} dot={false} name="CPA" />
      </LineChart>
    </ResponsiveContainer>
  );
}
