import Link from 'next/link';
import { AdsViews } from '@/components/ads-views';
import { ProfileRead } from '@/components/profile-read';
import { ScopeLabel } from '@/components/scope-label';
import { ThemesView } from '@/components/themes-view';
import { api } from '@/lib/api';
import { formatters } from '@/lib/format';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Ads by cluster' };

// The clustering approach from experiment 5 (tag-clusters) as a tab of its own: the resulting
// clusters and their videos; click one to see its insights. How the clusters were built stays on
// the experiment page.
const SLUG = 'tag-clusters';
const AXIS = 'cluster';
// The experiment's rules: $200+ lifetime spend, the account's last 30 days.
const MIN_SPEND = 200;
const WINDOW = 'last_30d';
const BASE = '/clusters';

export default async function Clusters({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireAuth();
  const cluster = (await searchParams).cluster ?? null;
  const [experiments, accounts] = await Promise.all([api.experiments(), api.accounts()]);
  const exp = experiments.find((e) => e.slug === SLUG);
  const fmt = formatters(accounts.find((a) => a.status === 'active')?.currency ?? 'USD');
  const [view, read] = exp
    ? await Promise.all([
        cluster ? Promise.resolve(null) : api.experimentThemes(exp.id, AXIS, MIN_SPEND, WINDOW).catch(() => null),
        cluster ? api.experimentProfileRead(exp.id, cluster).catch(() => null) : Promise.resolve(null),
      ])
    : [null, null];

  if (cluster) {
    const hasRead = read?.status === 'done' && (read.reviewed?.length ?? 0) > 0;
    return (
      <div className="space-y-6">
        <Link href={BASE} className="text-sm text-muted-foreground hover:text-foreground">
          ← Ads by cluster
        </Link>
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight text-balance">{cluster}</h1>
          <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            What separates its best videos from its worst <ScopeLabel>Last 30 days · every video watched</ScopeLabel>
          </p>
        </div>
        {hasRead ? (
          <ProfileRead data={read!} f={fmt} />
        ) : (
          <p className="max-w-prose text-muted-foreground">
            No insights for this cluster yet: its videos have not been watched and compared on what they say and show.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Ads</h1>
          <AdsViews current="/clusters" />
        </div>
        <p className="max-w-prose text-muted-foreground">
          Every tagged video grouped by who carries it (talking head, expert, interview, text only…) and when the product first
          appears, with all AI-made videos as one cluster. Open a cluster to see what separates its best videos from its worst.
        </p>
      </div>
      {view ? (
        <ThemesView data={view} fmt={fmt} axis={AXIS} base={BASE} minSpend={MIN_SPEND} window={WINDOW} defaultWindow={WINDOW} fixed />
      ) : (
        <p className="max-w-prose text-muted-foreground">No clusters yet for this account.</p>
      )}
    </div>
  );
}
