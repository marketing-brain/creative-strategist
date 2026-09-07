import { FileText } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Briefs' };

const formatWeek = (iso: string) => new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric' }).format(new Date(iso));

export default async function Briefs() {
  await requireAuth();
  const briefs = await api.briefs();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Weekly briefs</h1>
        <p className="text-sm text-muted-foreground">A written summary of what changed and what to test next, sent every Monday.</p>
      </div>
      {briefs.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-20 text-center">
          <FileText className="size-8 text-muted-foreground" aria-hidden="true" />
          <p className="max-w-sm text-sm text-muted-foreground">The first brief is written next Monday.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {briefs.map((b) => (
            <Card key={b.id}>
              <CardHeader>
                <CardTitle>Week of {formatWeek(b.week_start)}</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="font-sans text-sm leading-relaxed whitespace-pre-wrap">{b.content_md}</pre>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
