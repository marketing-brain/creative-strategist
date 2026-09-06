import { BatteryWarning } from 'lucide-react';
import { InsightCard } from '@/components/insight-card';
import { api } from '@/lib/api';

export const metadata = { title: 'Fatigue' };

export default async function Fatigue() {
  const alerts = await api.fatigue();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Fatigue alerts</h1>
        <p className="text-sm text-muted-foreground">Creatives whose performance is declining and may need a refresh.</p>
      </div>
      {alerts.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-20 text-center">
          <BatteryWarning className="size-8 text-muted-foreground" aria-hidden="true" />
          <p className="max-w-sm text-sm text-muted-foreground">No fatigue detected. We will flag creatives here as their performance drops.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {alerts.map((i) => (
            <InsightCard key={i.id} insight={i} />
          ))}
        </div>
      )}
    </div>
  );
}
