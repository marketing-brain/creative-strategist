import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { Insight } from '@/lib/types';

const pct = (x: number) => `${x >= 0 ? '+' : '-'}${Math.round(Math.abs(x) * 100)}%`;

export function InsightCard({ insight: i }: { insight: Insight }) {
  const isFatigue = i.kind === 'fatigue';
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between">
        <CardTitle className="text-base">
          {isFatigue ? <Link href={`/creatives/${i.creative_id}`} className="underline">{i.creative_headline ?? i.value}</Link> : `${i.dimension} = ${i.value}`}
        </CardTitle>
        <span className={`rounded px-2 py-1 text-sm font-semibold ${i.lift >= 0 ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>{pct(i.lift)} {i.metric.toUpperCase()}</span>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="text-muted-foreground">{i.sample_creatives} creatives · ${Math.round(i.sample_spend).toLocaleString()} spend · {i.window_start} to {i.window_end}</p>
        <p>{i.explanation}</p>
        <p><span className="font-medium">Next test:</span> {i.suggested_test}</p>
        {!isFatigue && <Link href={`/generate?insight=${i.id}`} className="inline-block rounded border px-3 py-1 hover:bg-muted">Generate from this</Link>}
      </CardContent>
    </Card>
  );
}
