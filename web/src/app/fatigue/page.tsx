import { InsightCard } from '@/components/insight-card';
import { api } from '@/lib/api';

export default async function Fatigue() {
  const alerts = await api.fatigue();
  return <div className="space-y-4"><h1 className="text-xl font-semibold">Fatigue alerts</h1>
    {alerts.length === 0 && <p className="text-sm text-muted-foreground">No fatigue detected.</p>}
    <div className="grid gap-4 md:grid-cols-2">{alerts.map((i) => <InsightCard key={i.id} insight={i} />)}</div></div>;
}
