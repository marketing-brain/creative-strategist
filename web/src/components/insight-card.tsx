import Link from 'next/link';
import { TrendingDown, TrendingUp } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from 'cn';
import type { Insight } from '@/lib/types';

const pct = (x: number) => `${x >= 0 ? '+' : '-'}${Math.round(Math.abs(x) * 100)}%`;

export function InsightCard({ insight: i }: { insight: Insight }) {
  const isFatigue = i.kind === 'fatigue';
  const positive = i.lift >= 0;
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <CardTitle className="text-base">
          {isFatigue ? (
            <Link href={`/creatives/${i.creative_id}`} className="underline decoration-muted-foreground/50 underline-offset-2 hover:decoration-foreground">
              {i.creative_headline ?? i.value}
            </Link>
          ) : (
            `${i.dimension} = ${i.value}`
          )}
        </CardTitle>
        <span
          className={cn(
            'flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-sm font-semibold tabular-nums',
            positive ? 'bg-success/10 text-success' : 'bg-destructive/10 text-destructive'
          )}
        >
          {positive ? <TrendingUp className="size-3.5" aria-hidden="true" /> : <TrendingDown className="size-3.5" aria-hidden="true" />}
          {pct(i.lift)} {i.metric.toUpperCase()}
        </span>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="text-muted-foreground">
          {i.sample_creatives} creatives · ${Math.round(i.sample_spend).toLocaleString()} spend · {i.window_start} to {i.window_end}
        </p>
        <p>{i.explanation}</p>
        <p>
          <span className="font-medium">Next test:</span> {i.suggested_test}
        </p>
      </CardContent>
    </Card>
  );
}
