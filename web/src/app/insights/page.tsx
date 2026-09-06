import { InsightCard } from '@/components/insight-card';
import { api } from '@/lib/api';

export default async function Insights() {
  const insights = await api.insights('pattern');
  return <div className="space-y-4"><h1 className="text-xl font-semibold">Insights</h1>
    {insights.length === 0 && <p className="text-sm text-muted-foreground">No insights yet. They appear after the nightly analysis once enough tagged creatives have spend.</p>}
    <div className="grid gap-4 md:grid-cols-2">{insights.map((i) => <InsightCard key={i.id} insight={i} />)}</div></div>;
}
