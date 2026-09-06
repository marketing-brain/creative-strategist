import { CreativeCard } from '@/components/creative-card';
import { CreativeFilters } from '@/components/creative-filters';
import { api } from '@/lib/api';

export default async function Creatives({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const q = new URLSearchParams();
  if (sp.account_id) q.set('account_id', sp.account_id);
  if (sp.format) q.set('format', sp.format);
  const [creatives, accounts] = await Promise.all([api.creatives(q.toString() ? `?${q}` : ''), api.accounts()]);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h1 className="text-xl font-semibold">Creatives</h1><CreativeFilters accounts={accounts} /></div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-6">{creatives.map((c) => <CreativeCard key={c.id} creative={c} />)}</div>
    </div>
  );
}
