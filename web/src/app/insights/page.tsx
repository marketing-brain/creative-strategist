import { Sparkles } from 'lucide-react';
import { InsightCard } from '@/components/insight-card';
import { api } from '@/lib/api';

export const metadata = { title: 'Insights' };

export default async function Insights() {
  const insights = await api.insights('pattern');
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
        <p className="text-sm text-muted-foreground">Patterns the nightly analysis found across your tagged, spending creatives.</p>
      </div>
      {insights.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-20 text-center">
          <Sparkles className="size-8 text-muted-foreground" aria-hidden="true" />
          <p className="max-w-sm text-sm text-muted-foreground">
            No insights yet. They appear after the nightly analysis once enough tagged creatives have spend.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {insights.map((i) => (
            <InsightCard key={i.id} insight={i} />
          ))}
        </div>
      )}
    </div>
  );
}
