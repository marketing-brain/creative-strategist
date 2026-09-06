'use client';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MetricDay } from '@/lib/types';

export function MetricChart({ rows }: { rows: MetricDay[] }) {
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No metrics yet.</p>;
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={rows}>
        <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="day" /><YAxis yAxisId="l" /><YAxis yAxisId="r" orientation="right" /><Tooltip />
        <Line yAxisId="l" type="monotone" dataKey="ctr" stroke="#2563eb" dot={false} name="CTR" />
        <Line yAxisId="r" type="monotone" dataKey="cpa" stroke="#dc2626" dot={false} name="CPA" />
      </LineChart>
    </ResponsiveContainer>
  );
}
