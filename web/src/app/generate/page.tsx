import Link from 'next/link';
import { GenerateForm } from '@/components/generate-form';
import { api } from '@/lib/api';

export default async function Generate({ searchParams }: { searchParams: Promise<{ insight?: string; created?: string }> }) {
  const sp = await searchParams;
  const [insights, generations, me] = await Promise.all([api.insights('pattern'), api.generations(), api.me()]);
  const options = insights.map((i) => ({ id: i.id, label: `${i.dimension} = ${i.value} (${i.lift >= 0 ? '+' : ''}${Math.round(i.lift * 100)}% ${i.metric.toUpperCase()})` }));
  const isAdmin = me.role === 'admin';
  return (
    <div className="grid gap-8 md:grid-cols-2">
      <section><h1 className="mb-3 text-xl font-semibold">Generate</h1>
        {isAdmin
          ? <GenerateForm insightId={sp.insight ?? null} insights={options} />
          : <p className="text-sm text-muted-foreground">Only admins can start a generation. Ask an admin on your team to run one.</p>}
        {sp.created && <p className="mt-3 text-sm">Generation queued. Refresh to see progress; review drafts on the <Link href="/review" className="underline">Review</Link> page.</p>}</section>
      <section><h2 className="mb-3 font-semibold">Recent generations</h2>
        <ul className="divide-y rounded border text-sm">{generations.map((g) => <li key={g.id} className="flex justify-between p-2"><span>{g.kind} · {g.concept ?? 'from insight'}</span><span className="text-muted-foreground">{g.status}{g.error ? ` — ${g.error}` : ''}</span></li>)}</ul></section>
    </div>
  );
}
