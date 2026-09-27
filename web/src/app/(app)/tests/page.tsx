import Link from 'next/link';
import { ChevronRight, FlaskConical } from 'lucide-react';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Tests' };

export default async function Tests() {
  await requireAuth();
  const tests = await api.experiments('test');
  return (
    <div className="max-w-4xl space-y-8">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Tests</h1>
        <p className="max-w-prose text-sm text-muted-foreground">
          New versions launched against the original in one ad set, each judged on the step it was made to change.
        </p>
      </div>
      {tests.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-16 text-center">
          <FlaskConical className="size-7 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm text-muted-foreground">No tests yet.</p>
          <Link href="/make" className="text-sm font-medium text-primary hover:underline">Make new versions of a video</Link>
        </div>
      ) : (
        <ul className="grid gap-3">
          {tests.map((t) => (
            <li key={t.id}>
              <Link
                href={`/experiments/${t.id}`}
                className="group flex items-center gap-4 rounded-xl border border-border bg-card px-4 py-4 transition-shadow hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-base font-semibold">{t.title}</p>
                  <p className="text-sm text-muted-foreground tabular-nums">
                    {t.steps_done === t.steps ? 'Finished' : `Step ${t.steps_done + 1} of ${t.steps}${t.current_step ? `: ${t.current_step}` : ''}`}
                  </p>
                  {t.waiting_on && (
                    <p className="text-sm">
                      <span className="font-medium text-chart-2">Waiting on you:</span> {t.waiting_on}
                    </p>
                  )}
                </div>
                <ChevronRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
