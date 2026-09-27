import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft, ChevronRight, ImageOff, PlayCircle } from 'lucide-react';
import { cn } from 'cn';
import { AdSetPicker } from '@/components/adset-picker';
import { AdSetReadView } from '@/components/adset-read';
import { ExperimentRules } from '@/components/experiment-rules';
import { PairedPatternsView } from '@/components/paired-patterns';
import { THEME_AXES, ThemesView } from '@/components/themes-view';
import { ClusterCompare } from '@/components/cluster-compare';
import { ScopeLabel } from '@/components/scope-label';
import { ProfileRead } from '@/components/profile-read';
import { ExperimentStep } from '@/components/experiment-step';
import { LiveRefresh } from '@/components/live-refresh';
import { api } from '@/lib/api';
import { blobUrl } from '@/lib/blob-url';
import { formatters } from '@/lib/format';
import { requireAuth } from '@/lib/require-auth.server';
import type { Experiment, ExperimentItem } from '@/lib/types';

export const metadata = { title: 'Experiment' };

const pct = (n: number | null) => (n == null ? '–' : `${Math.round(n * 100)}%`);

function FileTile({ item, groupName, labels, describes }: { item: ExperimentItem; groupName?: string; labels?: string[]; describes: boolean }) {
  const poster = blobUrl(item.poster_url);
  const body = (
    <>
      <div className="relative aspect-[4/5] w-full overflow-hidden rounded-md bg-muted">
        {poster ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={poster} alt="" loading="lazy" className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            <ImageOff className="size-5" aria-hidden="true" />
          </div>
        )}
        {item.format === 'video' && (
          <span className="absolute top-1.5 right-1.5 inline-flex items-center gap-1 rounded-full bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white tabular-nums">
            <PlayCircle className="size-3" aria-hidden="true" />
            {item.duration_s != null ? `${Math.round(item.duration_s)}s` : ''}
          </span>
        )}
      </div>
      <div className="min-w-0 space-y-1">
        <div translate="no" title={item.name ?? ''} className="line-clamp-2 text-xs leading-snug font-medium [overflow-wrap:anywhere]">
          {item.name ?? 'Untitled'}
        </div>
        <div className="flex flex-wrap gap-1">
          {labels?.map((l) => (
            <span key={l} className="rounded bg-secondary px-1.5 py-0.5 text-[10px] font-medium [overflow-wrap:anywhere] text-secondary-foreground">
              {l}
            </span>
          ))}
          {groupName && <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">{groupName}</span>}
        </div>
        {describes &&
          (item.format_answer ? (
            <p className="line-clamp-4 text-xs text-muted-foreground" title={item.format_answer}>
              {item.format_answer}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground/70 italic">Not described yet</p>
          ))}
      </div>
    </>
  );
  const cls = 'flex flex-col gap-2 rounded-lg border border-border bg-card p-2';
  return item.creative_id ? (
    <Link
      href={`/creatives/${item.creative_id}`}
      className={cn(cls, 'transition-shadow hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none')}
    >
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

function Score({ exp }: { exp: Experiment }) {
  if (!exp.score) return null;
  const s = exp.score;
  const families = [...new Set(exp.items.map((i) => i.label))];
  const count = (label: string, group: string) => exp.items.filter((i) => i.label === label && i.group_id === group).length;
  const stats = [
    {
      label: 'Groups hold one ad set',
      value: pct(s.purity),
      hint: 'Share of files that sit with the most common ad set in their group. 100% means no group mixes ad sets.',
    },
    {
      label: 'Ad sets stay together',
      value: pct(s.completeness),
      hint: 'Share of files in the group that holds most of their ad set. 100% means no ad set is split.',
    },
    {
      label: 'Agreement beyond chance',
      value: s.ari == null ? '–' : s.ari.toFixed(2),
      hint: 'Adjusted Rand index: 0 is what random groups would score, 1 is identical to your ad sets.',
    },
  ];
  return (
    <section className="space-y-3" aria-labelledby="score-title">
      <h2 id="score-title" className="text-lg font-semibold">
        How the groups line up with your ad sets
      </h2>
      <dl className="grid gap-3 sm:grid-cols-3">
        {stats.map((x) => (
          <div key={x.label} className="rounded-xl border border-border px-4 py-3">
            <dt className="text-xs text-muted-foreground">{x.label}</dt>
            <dd className="text-2xl font-semibold tabular-nums">{x.value}</dd>
            <p className="mt-1 text-xs text-muted-foreground">{x.hint}</p>
          </div>
        ))}
      </dl>
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th className="px-3 py-2 font-medium">Your ad set ↓ · Discovered group →</th>
              {exp.groups.map((g) => (
                <th key={g.id} className="px-3 py-2 text-right font-medium" title={g.definition ?? ''}>
                  {g.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {families.map((f) => (
              <tr key={f} className="border-b border-border last:border-0">
                <td className="px-3 py-2 font-medium whitespace-nowrap">{f}</td>
                {exp.groups.map((g) => {
                  const n = count(f, g.id);
                  return (
                    <td key={g.id} className={cn('px-3 py-2 text-right tabular-nums', n === 0 ? 'text-muted-foreground/40' : 'font-medium')}>
                      {n || '·'}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default async function ExperimentPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireAuth();
  const { id } = await params;
  const sp = await searchParams;
  const exp = await api.experiment(id).catch(() => null);
  if (!exp) notFound();

  const running = exp.steps.some((s) => s.status === 'running');
  // The ad set read: an experiment whose steps read one ad set at a time gets a picker.
  const readsAdSets = exp.steps.some((st) => st.key === 'against_account');
  const adset = readsAdSets ? (sp.adset ?? null) : null;
  const [adsets, adsetRead, accounts, patterns] = readsAdSets
    ? await Promise.all([
        api.experimentAdsets(exp.id),
        adset ? api.experimentAdsetRead(exp.id, adset).catch(() => null) : Promise.resolve(null),
        api.accounts(),
        api.experimentPatterns(exp.id).catch(() => null),
      ])
    : [[], null, [], null];
  const fmt = formatters(accounts.find((a) => a.status === 'active')?.currency ?? 'USD');
  // The themes view: an experiment whose steps sort videos into the buyer's themes.
  const sortsThemes = exp.steps.some((st) => st.key === 'assign' || st.key === 'cluster');
  const defaultAxis = exp.steps.some((st) => st.key === 'cluster') ? 'cluster' : 'format';
  // The tag-cluster experiment is fixed at the last 30 days and $200+ lifetime spend (its rules
  // say so); the themes experiment keeps its toggles (?min_spend=200, ?window=).
  const fixedScope = defaultAxis === 'cluster';
  const minSpend = fixedScope ? 200 : Math.max(0, Number(sp.min_spend) || 0);
  const themeAxis = (THEME_AXES as readonly string[]).includes(sp.axis ?? '') ? sp.axis! : defaultAxis;
  // Clusters read over the last 30 days by default (the creative page's window); the themes
  // experiment keeps its first-7-day default.
  const defaultWindow = defaultAxis === 'cluster' ? 'last_30d' : 'first_7d';
  const themeWindow = !fixedScope && (sp.window === 'first_7d' || sp.window === 'last_30d') ? sp.window : defaultWindow;
  // Clusters against each other: the tag-cluster experiment only, and not while one cluster is open.
  const comparesClusters = defaultAxis === 'cluster' && !sp.cluster;
  const [themeView, clusterRead, clusterCompare, profileRead] = sortsThemes
    ? await Promise.all([
        // One cluster open: its read stands alone, "← All clusters" goes back to the list.
        sp.cluster ? Promise.resolve(null) : api.experimentThemes(exp.id, themeAxis, minSpend, themeWindow).catch(() => null),
        sp.cluster ? api.experimentClusterRead(exp.id, sp.cluster, themeAxis, minSpend, themeWindow).catch(() => null) : Promise.resolve(null),
        comparesClusters ? api.experimentClusterCompare(exp.id, themeAxis, minSpend, themeWindow).catch(() => null) : Promise.resolve(null),
        sp.cluster && defaultAxis === 'cluster' && themeAxis === 'cluster'
          ? api.experimentProfileRead(exp.id, sp.cluster).catch(() => null)
          : Promise.resolve(null),
      ])
    : [null, null, null, null];
  const clustersHref = (() => {
    const q = new URLSearchParams({ axis: themeAxis });
    if (minSpend) q.set('min_spend', String(minSpend));
    if (themeWindow !== defaultWindow) q.set('window', themeWindow);
    return `/experiments/${exp.id}?${q}`;
  })();
  const hasGroups = exp.groups.length > 0;
  const view = hasGroups && sp.view !== 'families' ? 'groups' : 'families';
  const families = [...new Set(exp.items.map((i) => i.label))].map((label) => ({ label, items: exp.items.filter((i) => i.label === label) }));
  families.sort((a, b) => b.items.length - a.items.length);
  const groupName = new Map(exp.groups.map((g) => [g.id, g.name]));
  // A file in several ad sets appears once per ad set; count and group it once.
  const files = [...new Map(exp.items.map((i) => [i.id, i])).values()];
  const labelsOf = new Map<string, string[]>();
  for (const i of exp.items) labelsOf.set(i.id, [...(labelsOf.get(i.id) ?? []), i.label]);
  const described = files.filter((i) => i.format_answer).length;
  // Only an experiment with a describe step shows the model's answers under each file.
  const describes = exp.steps.some((st) => st.key === 'describe');
  const family = sp.family && families.some((f) => f.label === sp.family) ? sp.family : null;

  const sections =
    view === 'groups'
      ? exp.groups.map((g) => ({ key: g.id, title: g.name, note: g.definition, items: files.filter((i) => i.group_id === g.id) }))
      : families.filter((f) => !family || f.label === family).map((f) => ({ key: f.label, title: f.label, note: null, items: f.items }));

  const q = (over: Record<string, string | null>) => {
    const p = new URLSearchParams();
    if (view === 'families' && hasGroups) p.set('view', 'families');
    if (family) p.set('family', family);
    for (const [k, v] of Object.entries(over)) {
      if (v === null) p.delete(k);
      else p.set(k, v);
    }
    const s = p.toString();
    return `/experiments/${exp.id}${s ? `?${s}` : ''}`;
  };

  return (
    <div className="space-y-8">
      <LiveRefresh active={running} />
      <div className="space-y-2">
        <Link href={exp.kind === 'test' ? '/tests' : '/experiments'} className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" aria-hidden="true" />
          {exp.kind === 'test' ? 'Tests' : 'Research'}
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight text-balance">{exp.title}</h1>
        <p className="max-w-prose">{exp.question}</p>
        {!readsAdSets && !sortsThemes && <p className="max-w-prose text-sm text-muted-foreground">{exp.method}</p>}
      </div>

      {readsAdSets || sortsThemes ? (
        <>
          {clusterRead && (
            <section className="space-y-4 rounded-xl border border-border px-4 py-5" aria-labelledby="cluster-read-title">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 id="cluster-read-title" className="text-lg font-semibold">
                  {clusterRead.cluster?.name}
                </h2>
                <Link href={clustersHref} className="text-sm text-muted-foreground hover:text-foreground">
                  ← All clusters
                </Link>
              </div>
              {profileRead?.status === 'done' && (profileRead.reviewed?.length ?? 0) > 0 ? (
                <div className="space-y-3">
                  <div className="max-w-[68ch] space-y-1">
                    <h3 className="flex flex-wrap items-center gap-2 text-base font-semibold">
                      What separates the best from the worst <ScopeLabel>Last 30 days · every video watched</ScopeLabel>
                    </h3>
                    <p className="text-sm leading-6 text-muted-foreground">
                      Every video in the cluster was watched and described without its numbers, then compared on its content: the offer,
                      the argument, who vouches for it, the tone. Each card shows its test&apos;s p-value; leads did not clear the bar.
                    </p>
                  </div>
                  <ProfileRead data={profileRead} f={fmt} />
                </div>
              ) : (
                <p className="max-w-[68ch] text-sm leading-6 text-muted-foreground">
                  Content not read yet. This cluster&apos;s videos have not been watched and compared on what they say and show.
                </p>
              )}
              {/* The per-video table folds itself under "Every number behind these". */}
              <div className="border-t border-border pt-4">
                <AdSetReadView read={clusterRead} fmt={fmt} tableOnly />
              </div>
            </section>
          )}
          {clusterCompare && <ClusterCompare data={clusterCompare} fmt={fmt} readHref={(key) => `${clustersHref}&cluster=${encodeURIComponent(key)}`} />}
          {themeView && (
            <ThemesView
              data={themeView}
              fmt={fmt}
              axis={themeAxis}
              base={`/experiments/${exp.id}`}
              minSpend={minSpend}
              window={themeWindow}
              defaultWindow={defaultWindow}
              fixed={fixedScope}
            />
          )}
          {readsAdSets && (
            <>
              <section className="space-y-4" aria-labelledby="read-title">
                <div className="space-y-2">
                  <h2 id="read-title" className="text-lg font-semibold">
                    Read one ad set
                  </h2>
                  <p className="max-w-prose text-sm text-muted-foreground">
                    Pick an ad set. Everything below is computed from its own delivery; no model is involved.
                  </p>
                  <AdSetPicker adsets={adsets} value={adset} />
                </div>
                {adset && !adsetRead && <p className="text-sm text-muted-foreground">No files with delivery in that ad set.</p>}
                {adsetRead && <AdSetReadView read={adsetRead} fmt={fmt} />}
              </section>

              {patterns && <PairedPatternsView data={patterns} fmt={fmt} />}
            </>
          )}

          {/* How the experiment was run: for checking, not for reading first. */}
          <details className="group rounded-xl border border-border">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
              <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden="true" />
              How this experiment works: steps, rules and files
            </summary>
            <div className="space-y-8 border-t border-border px-4 py-5">
              <p className="max-w-prose text-sm text-muted-foreground">{exp.method}</p>
              <section aria-labelledby="steps-title" className="rounded-xl border border-border px-5 py-5">
                <h2 id="steps-title" className="sr-only">
                  Steps
                </h2>
                <ol>
                  {exp.steps.map((s, i) => (
                    <ExperimentStep key={s.key} step={s} n={i + 1} last={i === exp.steps.length - 1} />
                  ))}
                </ol>
              </section>
              <ExperimentRules rules={exp.rules} />

              <Score exp={exp} />

              <section className="space-y-4" aria-labelledby="files-title">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                  <div>
                    <h2 id="files-title" className="text-lg font-semibold">
                      The files
                    </h2>
                    <p className="text-sm text-muted-foreground tabular-nums">
                      {files.length} files{describes && <> · {described} described. Under each: the model&apos;s answer to “how is the ad made?”</>}
                    </p>
                  </div>
                  {hasGroups && (
                    <div className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5 text-sm" role="group" aria-label="Arrange files by">
                      {(['groups', 'families'] as const).map((v) => (
                        <Link
                          key={v}
                          href={`/experiments/${exp.id}${v === 'families' ? '?view=families' : ''}`}
                          aria-current={view === v ? 'true' : undefined}
                          className={cn(
                            'rounded-md px-2.5 py-1 font-medium',
                            view === v ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground',
                          )}
                        >
                          {v === 'groups' ? 'Discovered groups' : 'Your ad sets'}
                        </Link>
                      ))}
                    </div>
                  )}
                </div>
                {view === 'families' && (
                  <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show ad set">
                    <Link
                      href={q({ family: null })}
                      aria-current={!family ? 'true' : undefined}
                      className={cn(
                        'rounded-full border px-2.5 py-0.5 text-xs font-medium',
                        !family ? 'border-foreground bg-foreground text-background' : 'border-border text-muted-foreground hover:text-foreground',
                      )}
                    >
                      All
                    </Link>
                    {families.map((f) => (
                      <Link
                        key={f.label}
                        href={q({ family: f.label })}
                        aria-current={family === f.label ? 'true' : undefined}
                        className={cn(
                          'rounded-full border px-2.5 py-0.5 text-xs font-medium tabular-nums',
                          family === f.label ? 'border-foreground bg-foreground text-background' : 'border-border text-muted-foreground hover:text-foreground',
                        )}
                      >
                        {f.label} {f.items.length}
                      </Link>
                    ))}
                  </div>
                )}
                {sections.map((sec) => (
                  <div key={sec.key} className="space-y-2">
                    <div>
                      <h3 className="font-medium">
                        {sec.title} <span className="text-sm font-normal text-muted-foreground tabular-nums">{sec.items.length}</span>
                      </h3>
                      {sec.note && <p className="max-w-prose text-sm text-muted-foreground">{sec.note}</p>}
                      {view === 'families' && sec.items.some((i) => i.label_source) && (
                        <p className="max-w-prose text-xs text-muted-foreground">
                          {sec.items.filter((i) => i.label_source).length} of these files also ran in another TESTING ad set.
                        </p>
                      )}
                    </div>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
                      {sec.items.map((it) => (
                        <FileTile
                          key={it.id}
                          item={it}
                          labels={view === 'groups' ? labelsOf.get(it.id) : undefined}
                          describes={describes}
                          groupName={view === 'families' && it.group_id ? groupName.get(it.group_id) : undefined}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </section>
            </div>
          </details>
        </>
      ) : (
        <>
          <section aria-labelledby="steps-title" className="rounded-xl border border-border px-5 py-5">
            <h2 id="steps-title" className="sr-only">
              Steps
            </h2>
            <ol>
              {exp.steps.map((s, i) => (
                <ExperimentStep key={s.key} step={s} n={i + 1} last={i === exp.steps.length - 1} />
              ))}
            </ol>
          </section>
          {exp.media && exp.media.length > 0 && (
            <section className="space-y-3" aria-labelledby="media-title">
              <h2 id="media-title" className="text-lg font-semibold">
                What it made
              </h2>
              <div className="grid gap-6 sm:grid-cols-2">
                {exp.media.map((m) => (
                  <figure key={m.path} className="space-y-2">
                    <video src={m.url} controls preload="metadata" className="max-h-[70vh] w-full rounded-lg bg-black" />
                    <figcaption className="space-y-1">
                      <p className="text-base font-semibold">{m.title}</p>
                      {m.note && <p className="text-sm text-muted-foreground">{m.note}</p>}
                      <a href={m.url} download className="text-sm font-medium text-primary hover:underline">
                        Download
                      </a>
                    </figcaption>
                  </figure>
                ))}
              </div>
            </section>
          )}
          {patterns && <PairedPatternsView data={patterns} fmt={fmt} />}
          <ExperimentRules rules={exp.rules} />

          <Score exp={exp} />

          <section className="space-y-4" aria-labelledby="files-title">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h2 id="files-title" className="text-lg font-semibold">
                  The files
                </h2>
                <p className="text-sm text-muted-foreground tabular-nums">
                  {files.length} files{describes && <> · {described} described. Under each: the model&apos;s answer to “how is the ad made?”</>}
                </p>
              </div>
              {hasGroups && (
                <div className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5 text-sm" role="group" aria-label="Arrange files by">
                  {(['groups', 'families'] as const).map((v) => (
                    <Link
                      key={v}
                      href={`/experiments/${exp.id}${v === 'families' ? '?view=families' : ''}`}
                      aria-current={view === v ? 'true' : undefined}
                      className={cn(
                        'rounded-md px-2.5 py-1 font-medium',
                        view === v ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground',
                      )}
                    >
                      {v === 'groups' ? 'Discovered groups' : 'Your ad sets'}
                    </Link>
                  ))}
                </div>
              )}
            </div>
            {view === 'families' && (
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show ad set">
                <Link
                  href={q({ family: null })}
                  aria-current={!family ? 'true' : undefined}
                  className={cn(
                    'rounded-full border px-2.5 py-0.5 text-xs font-medium',
                    !family ? 'border-foreground bg-foreground text-background' : 'border-border text-muted-foreground hover:text-foreground',
                  )}
                >
                  All
                </Link>
                {families.map((f) => (
                  <Link
                    key={f.label}
                    href={q({ family: f.label })}
                    aria-current={family === f.label ? 'true' : undefined}
                    className={cn(
                      'rounded-full border px-2.5 py-0.5 text-xs font-medium tabular-nums',
                      family === f.label ? 'border-foreground bg-foreground text-background' : 'border-border text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {f.label} {f.items.length}
                  </Link>
                ))}
              </div>
            )}
            {sections.map((sec) => (
              <div key={sec.key} className="space-y-2">
                <div>
                  <h3 className="font-medium">
                    {sec.title} <span className="text-sm font-normal text-muted-foreground tabular-nums">{sec.items.length}</span>
                  </h3>
                  {sec.note && <p className="max-w-prose text-sm text-muted-foreground">{sec.note}</p>}
                  {view === 'families' && sec.items.some((i) => i.label_source) && (
                    <p className="max-w-prose text-xs text-muted-foreground">
                      {sec.items.filter((i) => i.label_source).length} of these files also ran in another TESTING ad set.
                    </p>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
                  {sec.items.map((it) => (
                    <FileTile
                      key={it.id}
                      item={it}
                      labels={view === 'groups' ? labelsOf.get(it.id) : undefined}
                      describes={describes}
                      groupName={view === 'families' && it.group_id ? groupName.get(it.group_id) : undefined}
                    />
                  ))}
                </div>
              </div>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
