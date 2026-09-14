import { Sparkles } from 'lucide-react';
import type { CreativeInsight } from '@/lib/types';

// Sits above the diagnosis panel and follows its type scale: 14px label, 16px body. The
// paragraph is written once per creative, against the diagnosis window it names.
export function CreativeInsightCard({ insight }: { insight: CreativeInsight }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">Insight</h2>
        {insight.paragraph && insight.window_start && insight.window_end && (
          <span className="text-sm text-muted-foreground">{`Based on ${insight.window_start} to ${insight.window_end}`}</span>
        )}
      </div>
      <div className="flex gap-3.5 rounded-xl border border-border bg-card px-6 py-4">
        <Sparkles className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
        {insight.paragraph ? (
          <p className="text-base leading-relaxed text-pretty">{insight.paragraph}</p>
        ) : (
          <p className="text-sm leading-relaxed text-muted-foreground">
            No insight yet. It is written once this creative has enough delivery to compare against the account.
          </p>
        )}
      </div>
    </section>
  );
}
