import { api } from '@/lib/api';

export default async function Briefs() {
  const briefs = await api.briefs();
  return <div className="space-y-8"><h1 className="text-xl font-semibold">Weekly briefs</h1>
    {briefs.length === 0 && <p className="text-sm text-muted-foreground">The first brief is written next Monday.</p>}
    {briefs.map((b) => <article key={b.id} className="rounded border p-4"><h2 className="mb-2 font-semibold">Week of {b.week_start}</h2><pre className="whitespace-pre-wrap font-sans text-sm">{b.content_md}</pre></article>)}</div>;
}
