import Link from 'next/link';
import { CheckCircle2, Sparkles } from 'lucide-react';
import { GenerateForm } from '@/components/generate-form';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Generate' };

export default async function Generate({ searchParams }: { searchParams: Promise<{ insight?: string; created?: string }> }) {
  await requireAuth();
  const sp = await searchParams;
  const [insights, generations, me] = await Promise.all([api.insights('pattern'), api.generations(), api.me()]);
  const options = insights.map((i) => ({ id: i.id, label: `${i.dimension} = ${i.value} (${i.lift >= 0 ? '+' : ''}${Math.round(i.lift * 100)}% ${i.metric.toUpperCase()})` }));
  const isAdmin = me.role === 'admin';
  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <section className="space-y-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Generate</h1>
          <p className="text-sm text-muted-foreground">Create on-brand variants from a winning pattern or a fresh concept.</p>
        </div>
        {isAdmin ? (
          <GenerateForm insightId={sp.insight ?? null} insights={options} />
        ) : (
          <p className="text-sm text-muted-foreground">Only admins can start a generation. Ask an admin on your team to run one.</p>
        )}
        {sp.created && (
          <p className="flex items-center gap-2 rounded-lg bg-success/10 px-3 py-2 text-sm text-success">
            <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
            Generation queued. Refresh to see progress; review drafts on the{' '}
            <Link href="/review" className="underline underline-offset-2">
              Review
            </Link>{' '}
            page.
          </p>
        )}
      </section>
      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Recent generations</h2>
        {generations.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-16 text-center">
            <Sparkles className="size-8 text-muted-foreground" aria-hidden="true" />
            <p className="max-w-xs text-sm text-muted-foreground">Generations you start will show up here with their status.</p>
          </div>
        ) : (
          <Card size="sm">
            <CardHeader className="sr-only">
              <CardTitle>Recent generations</CardTitle>
            </CardHeader>
            <CardContent className="divide-y divide-border p-0">
              {generations.map((g) => (
                <div key={g.id} className="flex items-center justify-between px-4 py-2.5 text-sm">
                  <span>
                    {g.kind} · {g.concept ?? 'from insight'}
                  </span>
                  <span className="text-muted-foreground">
                    {g.status}
                    {g.error ? ` · ${g.error}` : ''}
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        )}
      </section>
    </div>
  );
}
