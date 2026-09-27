import Link from 'next/link';
import { ArrowRight, ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { cn } from 'cn';
import { Mark } from '@/components/account-read-evidence';
import { OpenFromHash } from '@/components/open-from-hash';
import { api } from '@/lib/api';
import { splitLede } from '@/lib/account-read-view';
import { formatters, type Formatters } from '@/lib/format';
import { shortTitle } from '@/lib/make-format';
import { requireAuth } from '@/lib/require-auth.server';
import type { FindingStatus, PlayChoice, PlayFinding, PlayGridRow, WorksFinding } from '@/lib/types';

export const metadata = { title: 'What works' };

// A playbook by creative choice. Each closed row carries what a marketer scans for (the verdict,
// the funnel step, the headline comparison, how sure we are, the spend it touches and the ads
// it touches); opening it shows the evidence per kind of video and where to try it. Code sets
// every tier; see cs.experiments.playbook. Compared with the category (Motion, Foreplay, Atria,
// Superads, Madgicx), the difference is that every comparison is within one kind of video.

const VERDICT: Record<string, { label: string; cls: string }> = {
  do: { label: 'Do', cls: 'border-success/40 bg-success/10 text-success' },
  avoid: { label: 'Avoid', cls: 'border-destructive/40 bg-destructive/10 text-destructive' },
  mixed: { label: 'Mixed', cls: 'border-chart-2/50 bg-chart-2/10 text-chart-2' },
  none: { label: 'Note', cls: 'border-border text-muted-foreground' },
};
const TIER: Record<PlayFinding['tier'], string> = { rule: 'Holds up', test: 'Lead', context: 'Context', story: 'One video' };
const STEP: Record<string, string> = { stop: 'Stopping the scroll', click: 'Clicking', buy: 'Buying' };
const STATUS_RANK: FindingStatus[] = ['untested', 'writing', 'briefed', 'made'];
const STATUS: Record<FindingStatus, { label: string; cls: string }> = {
  untested: { label: 'Untested', cls: 'border-border text-muted-foreground' },
  writing: { label: 'Writing a brief', cls: 'border-primary/40 text-primary' },
  briefed: { label: 'Brief written', cls: 'border-foreground/30 text-foreground' },
  made: { label: 'Versions made', cls: 'border-success/40 text-success' },
};
const TEST_STATE: Record<string, { label: string; cls: string }> = {
  confirmed: { label: 'Confirmed by a test', cls: 'border-success/40 bg-success/10 text-success' },
  failed: { label: 'Failed its test', cls: 'border-destructive/40 bg-destructive/10 text-destructive' },
  mixed: { label: 'Tests disagree', cls: 'border-chart-2/50 text-chart-2' },
  testing: { label: 'In a test', cls: 'border-primary/40 text-primary' },
};
const MEASURE: Record<string, string> = { stop: 'share still watching at 3s', click: 'click rate', buy: 'purchase rate' };
const pctOf = (x: number | null | undefined) => (x == null ? '–' : `${(x * 100).toFixed(1)}%`);
const FOCUS = 'focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none';
const dayFmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
const day = (iso: string) => dayFmt.format(new Date(`${iso.slice(0, 10)}T00:00:00Z`));
const anchor = (key: string) => `c-${key}`;
/** "Talking head · Late reveal (>10s)" → "Talking head, late reveal" for tight headers. */
const kindShort = (name: string) => name.replace(/\s*\(.*?\)\s*$/, '').replace(/ · (\w)/, (_, c: string) => `, ${c.toLowerCase()}`);

function splitValue(v: string): { main: string; vs: string | null; p: string | null } {
  const p = v.match(/\((p\s*[<=]?\s*[0-9.]+)\)\s*$/i);
  const rest = p ? v.slice(0, p.index).trim() : v;
  const [main, vs] = rest.split(/\s+vs\.?\s+/);
  return { main, vs: vs ?? null, p: p ? p[1] : null };
}

/** Green or red only when the direction is certain: a rate where higher is better, a cost
 *  where lower is better. Counts stay neutral. */
function tone(label: string, main: string, vs: string | null): 'good' | 'bad' | null {
  if (!vs) return null;
  const num = (s: string) => Number(s.replace(/[^0-9.\-]/g, ''));
  const a = num(main), b = num(vs);
  if (!Number.isFinite(a) || !Number.isFinite(b) || a === b) return null;
  if (/\$/.test(main) || /\b(cpa|cost)\b/i.test(label)) return a < b ? 'good' : 'bad';
  if (/%/.test(main) && !/\b(drop|leave|skip)\b/i.test(label)) return a > b ? 'good' : 'bad';
  return null;
}
const toneCls = (t: 'good' | 'bad' | null) => (t === 'good' ? 'text-success' : t === 'bad' ? 'text-destructive' : '');

function NumberStrip({ numbers }: { numbers: WorksFinding['numbers'] }) {
  if (!numbers.length) return null;
  return (
    <div
      className={cn(
        'grid divide-y divide-border overflow-hidden rounded-lg border border-border bg-card sm:divide-x sm:divide-y-0',
        numbers.length >= 4 ? 'sm:grid-cols-4' : numbers.length === 3 ? 'sm:grid-cols-3' : numbers.length === 2 ? 'sm:grid-cols-2' : '',
      )}
    >
      {numbers.map((n) => {
        const v = splitValue(n.value);
        return (
          <div key={n.label} className="flex items-baseline justify-between gap-3 px-3 py-2.5 sm:block sm:py-3">
            <div className="text-[11px] font-medium text-muted-foreground sm:mb-1.5">{n.label}</div>
            <div className="flex items-baseline gap-2 sm:block">
              <div className={cn('font-mono text-lg leading-none font-medium tabular-nums sm:text-xl', toneCls(tone(n.label, v.main, v.vs)))}>{v.main}</div>
              {(v.vs || v.p) && (
                <div className="font-mono text-[11px] text-muted-foreground tabular-nums sm:mt-1.5">
                  {v.vs && (
                    <>
                      <span className="text-muted-foreground/60">vs</span> {v.vs}
                    </>
                  )}
                  {v.p && <span className="ml-2 text-muted-foreground/60">{v.p}</span>}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Thumb({ url, className, size = 40 }: { url: string | null; className?: string; size?: number }) {
  return (
    <div className={cn('relative aspect-[4/5] shrink-0 overflow-hidden rounded bg-muted', className)}>
      {url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt="" width={size} height={Math.round(size * 1.25)} loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover" />
      )}
    </div>
  );
}

function Badge({ verdict, className }: { verdict: string; className?: string }) {
  const v = VERDICT[verdict] ?? VERDICT.none;
  return <span className={cn('inline-flex w-16 justify-center rounded-md border px-1.5 py-1 text-[13px] leading-none font-semibold', v.cls, className)}>{v.label}</span>;
}

/** Every choice worth acting on against every kind of video: where each was learned and which
 *  way it points. A mixed choice shows both colours on one row. */
function Glance({ grid, kinds }: { grid: PlayGridRow[]; kinds: string[] }) {
  const used = kinds.filter((k) => grid.some((g) => g.cells[k]));
  if (!grid.length || !used.length) return null;
  return (
    <section aria-labelledby="glance-title" className="space-y-2">
      <h2 id="glance-title" className="text-base font-semibold">At a glance</h2>
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[640px] text-[13px]">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th scope="col" className="px-3 py-2 font-medium">Choice</th>
              {used.map((k) => (
                <th key={k} scope="col" className="px-2 py-2 text-center font-medium">{kindShort(k)}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {grid.map((g) => (
              <tr key={g.key}>
                <th scope="row" className="px-3 py-2 text-left font-medium">
                  <a href={`#${anchor(g.key)}`} className={cn('rounded hover:underline', FOCUS)}>{g.name}</a>
                </th>
                {used.map((k) => {
                  const c = g.cells[k];
                  return (
                    <td key={k} className="px-2 py-2 text-center">
                      {c ? (
                        <span
                          className={cn(
                            'inline-flex min-w-14 justify-center rounded-sm border px-1.5 py-0.5 leading-none font-semibold',
                            c.direction === 'do' ? 'border-success/40 text-success' : 'border-destructive/40 text-destructive',
                            c.tier === 'rule' && (c.direction === 'do' ? 'bg-success/10' : 'bg-destructive/10'),
                          )}
                          title={`${c.direction === 'do' ? 'Helps' : 'Hurts'}, ${TIER[c.tier].toLowerCase()}`}
                        >
                          {c.direction === 'do' ? 'Do' : 'Avoid'}
                        </span>
                      ) : (
                        <span className="text-muted-foreground/40" aria-label="Not learned here">-</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[12px] text-muted-foreground">Filled: held up. Outlined: a lead. A dash means this kind of video said nothing about it.</p>
    </section>
  );
}

/** One kind of video's evidence on the choice, and where to try it. */
function Evidence({ f, fmt }: { f: PlayFinding; fmt: Formatters }) {
  const [lede, rest] = splitLede(f.body);
  const advice = f.tier === 'rule' || f.tier === 'test';
  return (
    <div className="grid gap-5 border-t border-border py-5 first:border-t-0 md:grid-cols-[minmax(0,1fr)_17rem]">
      <div className="min-w-0 space-y-4">
        {(f.tests ?? []).length > 0 && (
          <ul className="space-y-1 rounded-lg border border-border bg-muted/30 p-3 text-[13px]">
            {f.tests!.map((t, k) => (
              <li key={k}>
                <span className={cn('font-medium', t.verdict === 'won' ? 'text-success' : t.verdict === 'lost' ? 'text-destructive' : 'text-foreground')}>
                  Test {t.verdict === 'won' ? 'won' : t.verdict === 'lost' ? 'lost' : t.verdict === 'no difference' ? 'showed no difference' : t.verdict === 'not found' ? 'ads not found yet' : 'still running'}
                </span>{' '}
                on {MEASURE[t.measure]}
                {t.version?.rate != null && t.original?.rate != null && `: ${pctOf(t.version.rate)} against the original's ${pctOf(t.original.rate)}`}
                {t.experiment_id && (
                  <>
                    {' '}
                    <Link href={`/experiments/${t.experiment_id}`} className="text-primary hover:underline">
                      {t.test ?? 'the test'}
                    </Link>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        <div className="space-y-1">
          <p className="text-[13px] text-muted-foreground">
            In <span className="font-medium text-foreground">{kindShort(f.cluster)}</span> it{' '}
            <span className={f.direction === 'do' ? 'text-success' : 'text-destructive'}>{f.direction === 'do' ? 'helps' : 'hurts'}</span>.{' '}
            {TIER[f.tier]}
            {f.scripts ? `, ${f.scripts[0]} scripts with it and ${f.scripts[1]} without.` : '.'}
          </p>
          <h4 className="text-base leading-snug font-semibold text-balance">{f.title}</h4>
        </div>
        <NumberStrip numbers={f.numbers} />
        <p className="max-w-[68ch] border-l-2 border-border pl-4 text-[15px] leading-7">
          <Mark className="mr-2 text-muted-foreground">Why</Mark>
          {lede}
        </p>
        {(rest || f.basis) && (
          <details className="group/why">
            <summary className={cn('inline-flex cursor-pointer list-none items-center gap-1 rounded text-[13px] font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden', FOCUS)}>
              <ChevronRight className="size-3.5 transition-transform group-open/why:rotate-90 motion-reduce:transition-none" aria-hidden="true" />
              Full reasoning
            </summary>
            {rest && <p className="mt-3 max-w-[68ch] text-[15px] leading-7 text-pretty">{rest}</p>}
            {f.basis && <p className="mt-3 max-w-[68ch] text-[13px] leading-6 text-muted-foreground">Checked: {f.basis}</p>}
          </details>
        )}
      </div>
      {advice && (
        <aside className="space-y-3 self-start rounded-xl border border-border bg-card p-4">
          <p className="text-[13px] leading-5">
            <span className="font-semibold">{f.direction === 'avoid' ? 'Remove it from' : 'Add it to'}</span>{' '}
            <span className="text-muted-foreground">{f.direction === 'avoid' ? 'these videos that have it' : 'these videos that lack it'}</span>
          </p>
          <p className="text-[12px] leading-5 text-muted-foreground">Look for: {f.trait}</p>
          {f.targets.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">No video of this kind clearly {f.direction === 'avoid' ? 'has' : 'lacks'} it.</p>
          ) : (
            <ul className="space-y-1.5">
              {f.targets.map((v) => (
                <li key={v.footage_id}>
                  <Link href={`/make/${v.footage_id}?finding=${f.id}`} className={cn('group -m-1 flex items-center gap-2.5 rounded-md p-1 transition-colors hover:bg-muted', FOCUS)}>
                    <Thumb url={v.poster_url} className="w-8" size={32} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium" translate="no">{shortTitle(v.name)}</span>
                      <span className="block font-mono text-[11px] text-muted-foreground tabular-nums">{v.spend != null ? `${fmt.money(v.spend)} spend` : ''}</span>
                    </span>
                    <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary motion-reduce:transition-none" aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </aside>
      )}
    </div>
  );
}

function statusOf(c: PlayChoice): FindingStatus {
  return c.findings.reduce<FindingStatus>((best, f) => (STATUS_RANK.indexOf(f.status) > STATUS_RANK.indexOf(best) ? f.status : best), 'untested');
}

function ChoiceRow({ c, fmt }: { c: PlayChoice; fmt: Formatters }) {
  const status = statusOf(c);
  const st = STATUS[status];
  const verdict = c.section === 'mixed' ? 'mixed' : c.verdict;
  const head = c.headline ? splitValue(c.headline.value) : null;
  const t = c.headline && head ? tone(c.headline.label, head.main, head.vs) : null;
  const thumbs = c.findings.filter((f) => f.tier === 'rule' || f.tier === 'test').flatMap((f) => f.targets).slice(0, 3);
  const conf = c.confidence;
  const states = c.findings.map((f) => f.test?.state).filter(Boolean) as string[];
  const tested = ['failed', 'mixed', 'confirmed', 'testing'].find((x) => states.includes(x)) ?? null;
  return (
    <li id={anchor(c.key)} className="scroll-mt-6">
      <details className="group">
        <summary
          className={cn(
            'grid cursor-pointer list-none grid-cols-[4rem_minmax(0,1fr)] items-start gap-x-4 gap-y-3 px-4 py-4 transition-colors hover:bg-muted/40 [&::-webkit-details-marker]:hidden md:grid-cols-[4rem_minmax(0,1fr)_auto_1rem]',
            FOCUS,
            'focus-visible:ring-inset',
          )}
        >
          <Badge verdict={verdict} className="mt-0.5" />
          <div className="min-w-0 space-y-2">
            <h3 className="text-base leading-snug font-semibold">{c.name}</h3>
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[13px]">
              {c.step && <span className="font-medium text-muted-foreground">{STEP[c.step]}</span>}
              {c.headline && head && (
                <span className="tabular-nums">
                  <span className="text-muted-foreground">{c.headline.label.replace(/^(buy|click|stop)(ing)?,?\s*/i, '')}: </span>
                  <span className={cn('font-mono font-semibold', toneCls(t))}>{head.main}</span>
                  {head.vs && <span className="text-muted-foreground"> vs {head.vs}</span>}
                </span>
              )}
              <span className="text-muted-foreground">
                {TIER[conf.tier]}
                {conf.scripts ? ` on ${conf.scripts[0]} scripts against ${conf.scripts[1]}` : ''}
              </span>
            </div>
            {c.section === 'mixed' && (
              <p className="text-[13px] text-chart-2">Helps in one kind of video and hurts in another: test before using it.</p>
            )}
          </div>
          <div className="col-start-2 flex items-center gap-3 md:col-start-auto md:flex-col md:items-end md:gap-2">
            {c.stake && (
              <p className="text-[13px] tabular-nums md:text-right">
                <span className="font-semibold">{fmt.money(c.stake.spend)}</span>{' '}
                <span className="text-muted-foreground">
                  on {c.stake.videos} {c.stake.videos === 1 ? 'video' : 'videos'}{' '}
                  {c.section === 'mixed' ? 'a test would use' : c.stake.direction === 'avoid' ? 'that have it' : 'that lack it'}
                </span>
              </p>
            )}
            <div className="flex items-center gap-1.5">
              {thumbs.map((v) => <Thumb key={v.footage_id} url={v.poster_url} className="w-7" size={28} />)}
              {tested && (
                <span className={cn('ml-1 flex w-fit items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[12px] leading-none font-medium whitespace-nowrap', TEST_STATE[tested].cls)}>
                  {TEST_STATE[tested].label}
                </span>
              )}
              <span className={cn('ml-1 flex w-fit items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[12px] leading-none font-medium whitespace-nowrap', st.cls)}>
                {status === 'writing' && <Loader2 className="size-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />}
                {st.label}
              </span>
            </div>
          </div>
          <ChevronDown className="mt-1 hidden size-4 text-muted-foreground transition-transform group-open:rotate-180 motion-reduce:transition-none md:block" aria-hidden="true" />
        </summary>
        <div className="px-4 pb-4 md:pl-[6rem]">
          {c.findings.map((f) => <Evidence key={f.id} f={f} fmt={fmt} />)}
        </div>
      </details>
    </li>
  );
}

function Section({ id, title, lede, choices, fmt }: { id: string; title: string; lede: string; choices: PlayChoice[]; fmt: Formatters }) {
  if (!choices.length) return null;
  return (
    <section className="space-y-3" aria-labelledby={id}>
      <div className="space-y-1">
        <h2 id={id} className="flex items-baseline gap-2 text-base font-semibold">
          {title} <span className="font-normal text-muted-foreground tabular-nums">{choices.length}</span>
        </h2>
        <p className="max-w-prose text-[13px] text-muted-foreground">{lede}</p>
      </div>
      <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border">
        {choices.map((c) => <ChoiceRow key={c.key} c={c} fmt={fmt} />)}
      </ul>
    </section>
  );
}

function Notes({ title, choices }: { title: string; choices: PlayChoice[] }) {
  if (!choices.length) return null;
  return (
    <details className="group/notes rounded-xl border border-border">
      <summary className={cn('flex cursor-pointer list-none items-center gap-2 rounded-xl px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden', FOCUS)}>
        <ChevronRight className="size-4 text-muted-foreground transition-transform group-open/notes:rotate-90 motion-reduce:transition-none" aria-hidden="true" />
        {title} <span className="font-normal text-muted-foreground tabular-nums">{choices.reduce((n, c) => n + c.findings.length, 0)}</span>
      </summary>
      <ul className="divide-y divide-border border-t border-border">
        {choices.flatMap((c) =>
          c.findings.map((f) => (
            <li key={f.id} className="space-y-1 px-4 py-3 text-sm">
              <p className="font-medium">{f.title}</p>
              <p className="text-[13px] text-muted-foreground">
                {kindShort(f.cluster)}. {splitLede(f.body)[0]}
              </p>
            </li>
          )),
        )}
      </ul>
    </details>
  );
}

export default async function WhatWorks() {
  await requireAuth();
  const [w, accounts] = await Promise.all([api.whatWorks().catch(() => null), api.accounts().catch(() => [])]);
  const fmt = formatters(accounts.find((a) => a.status === 'active')?.currency ?? 'USD');
  const pb = w?.playbook;
  const by = (s: PlayChoice['section']) => pb?.choices.filter((c) => c.section === s) ?? [];
  const rules = by('rule');
  const mixed = by('mixed');
  const tests = by('test');

  return (
    <div className="max-w-5xl space-y-8">
      <OpenFromHash />
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">What works</h1>
        <p className="max-w-prose text-sm text-muted-foreground">
          Your creative playbook: what to do, what to avoid and what to test, learned by comparing videos of the same kind.
        </p>
      </div>

      {!pb ? (
        <section className="border-y border-border py-10 text-center text-sm text-muted-foreground">
          No playbook yet. It is built once each kind of video has been read.
        </section>
      ) : (
        <>
          <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground tabular-nums">
            {w?.span && (
              <span>
                Delivery {day(w.span.start)} to {day(w.span.end)}
              </span>
            )}
            {[`read ${day(pb.created_at)}`, `${rules.length} ${rules.length === 1 ? 'rule' : 'rules'}`, `${mixed.length} mixed`, `${tests.length} to test`].map((t) => (
              <span key={t}>
                <span className="mr-3 text-muted-foreground/40">/</span>
                {t}
              </span>
            ))}
          </p>

          <Glance grid={pb.grid} kinds={pb.kinds} />

          <Section id="rules" title="Rules" lede="Held up against the data, on at least three scripts each side. Follow these in every new version." choices={rules} fmt={fmt} />
          <Section id="mixed" title="Mixed" lede="Helps in one kind of video and hurts in another. Test before using it anywhere." choices={mixed} fmt={fmt} />
          <Section id="tests" title="Worth testing" lede="Point the right way on too few scripts to be sure. One test turns each into a rule or drops it." choices={tests} fmt={fmt} />

          <div className="space-y-3">
            <Notes title="What the videos of each kind share" choices={by('context')} />
            <Notes title="Stories about single videos" choices={by('story')} />
          </div>
          <p className="text-[13px] text-muted-foreground">
            Every new brief is written with the rules and shared traits of its kind of video. Open a row to see the evidence and the videos to try it on.
          </p>
          <Link
            href="/make?view=new"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
          >
            Make a brand-new video of one of these kinds <ArrowRight className="size-4" aria-hidden="true" />
          </Link>
        </>
      )}
    </div>
  );
}
