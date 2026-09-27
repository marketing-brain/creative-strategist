import Link from 'next/link';
import { ChevronRight, FlaskConical } from 'lucide-react';
import { cn } from 'cn';
import { api } from '@/lib/api';
import { requireAdmin } from '@/lib/require-admin.server';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Research' };

const CURRENT: Record<string, string> = {
  waiting: 'text-amber-700 dark:text-amber-400',
  running: 'text-primary',
  failed: 'text-destructive',
  pending: 'text-muted-foreground',
};

export default async function Experiments() {
  await requireAuth();
  await requireAdmin();
  const list = await api.experiments('research');
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Research</h1>
        <p className="mt-1 max-w-prose text-sm text-muted-foreground">
          Lab: how the analysis itself works, tested one step at a time on a fixed set of files. Visible to admins only.
        </p>
      </div>
      {list.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-16 text-center">
          <FlaskConical className="size-7 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm text-muted-foreground">No experiments yet.</p>
        </div>
      ) : (
        <ul className="grid gap-3">
          {list.map((e) => (
            <li key={e.id}>
              <Link
                href={`/experiments/${e.id}`}
                className="group flex items-center gap-4 rounded-xl border border-border bg-card px-4 py-3.5 transition-shadow hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="font-semibold">{e.title}</div>
                  <p className="line-clamp-2 max-w-prose text-sm text-muted-foreground">{e.question}</p>
                  <p className="text-xs text-muted-foreground tabular-nums">
                    {e.files} files · step {Math.min(e.steps_done + 1, e.steps)} of {e.steps}
                    {e.current_step && (
                      <>
                        {' '}
                        · <span className={cn('font-medium', CURRENT[e.current_status ?? 'pending'])}>{e.current_step}</span>
                        {e.current_status === 'waiting' && ' is waiting on you'}
                        {e.current_status === 'running' && ' is running'}
                      </>
                    )}
                  </p>
                </div>
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
