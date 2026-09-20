import { Sparkles } from 'lucide-react';
import type { CreativeInsight } from '@/lib/types';

// Why there is no paragraph, in the words of the gate that stopped it. The reason comes from
// the API, which asks the same gates the insight job obeys.
export function emptyInsightText(insight: CreativeInsight): string {
  switch (insight.reason) {
    case 'rejected':
      return 'No insight. A draft was written but failed the accuracy check, so it is not shown.';
    case 'no_asset':
      return "No insight yet. This creative's file has not been downloaded, so there is nothing to read.";
    case 'unresolved':
      return "No insight yet. This creative's format is not confirmed as image, video or carousel.";
    case 'format_limit':
      return `No insight yet. This workspace has reached its limit of ${insight.limit ?? 'allowed'} ${insight.format ?? 'creative'} insights.`;
    case 'lengths_pending':
      return 'No insight yet. Other videos in this account are still being measured for length, which the retention comparison needs.';
    case 'unreadable':
      return 'No insight yet. It is written once this creative has enough delivery to compare against the account.';
    case 'paused':
      return 'No insight yet. This creative qualifies, but the nightly insight run is paused.';
    case 'waiting':
      return 'No insight yet. This creative qualifies and will be picked up by the next nightly insight run.';
    default:
      return 'No insight yet.';
  }
}

// Sits above the diagnosis panel and follows its type scale: 14px label, 16px body. The
// paragraph is written once per creative, against the diagnosis window it names.
export function CreativeInsightCard({ insight }: { insight: CreativeInsight }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">Written analysis</h2>
        {insight.paragraph && insight.window_start && insight.window_end && (
          <span className="text-sm text-muted-foreground">{`Written once from ${insight.window_start} to ${insight.window_end}. The diagnosis above is current.`}</span>
        )}
      </div>
      <div className="flex gap-3.5 rounded-xl border border-border bg-card px-6 py-4">
        <Sparkles className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
        {insight.paragraph ? (
          <div className="space-y-3">
            {insight.paragraph
              .split(/\n\s*\n/)
              .filter((p) => p.trim())
              .map((p, i) => (
                <p key={i} className="text-base leading-relaxed text-pretty">
                  {p.trim()}
                </p>
              ))}
          </div>
        ) : (
          <p className="text-sm leading-relaxed text-muted-foreground">
            {emptyInsightText(insight)}
          </p>
        )}
      </div>
    </section>
  );
}
