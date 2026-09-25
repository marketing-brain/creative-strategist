import { Check, ChevronRight, X } from 'lucide-react';
import { cn } from 'cn';
import { label, words } from '@/components/tag-list';
import type { Formatters } from '@/lib/format';
import type { PairedPattern, PairedPatterns } from '@/lib/types';

const signed = (x: number) => `${x >= 0 ? '+' : '−'}${Math.round(Math.abs(x) * 100)}%`;

function Row({ p, fmt }: { p: PairedPattern; fmt: Formatters }) {
  const show = (x: number) => (p.metric === 'cpa' ? fmt.money(x) : fmt.pct(x, 1));
  const good = p.higher_is_better ? p.median_gap > 0 : p.median_gap < 0;
  return (
    <li>
      <details className="group">
        <summary className="flex cursor-pointer list-none items-start gap-3 px-4 py-3 hover:bg-accent/40 [&::-webkit-details-marker]:hidden">
          <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
          <div className="min-w-0 flex-1 space-y-0.5">
            <div className="text-sm">
              <span className="text-muted-foreground">{label(p.dimension)}:</span> <span className="font-medium">{words(p.value)}</span>
              <span className="text-muted-foreground">
                {' '}
                · {p.format === 'video' ? 'videos' : 'images'} · {p.metric_label}
              </span>
            </div>
            <div className="text-xs text-muted-foreground tabular-nums">
              Files with it did better in{' '}
              <span className="font-medium text-foreground">
                {p.wins} of {p.adsets}
              </span>{' '}
              ad sets. Typical gap <span className={cn('font-medium', good ? 'text-success' : 'text-destructive')}>{signed(p.median_gap)}</span>. Chance of a
              split this lopsided from a coin flip: {p.p < 0.001 ? '<0.1%' : `${(p.p * 100).toFixed(1)}%`}
              {p.q < 1 && `, ${(p.q * 100).toFixed(0)}% after correcting for the number of tests`}.
            </div>
          </div>
        </summary>
        <div className="overflow-x-auto px-4 pb-4 pl-11">
          <table className="w-full min-w-[34rem] text-xs">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-1 pr-3 font-medium">Ad set</th>
                <th className="py-1 pr-3 text-right font-medium">Files with / without</th>
                <th className="py-1 pr-3 text-right font-medium">With</th>
                <th className="py-1 pr-3 text-right font-medium">Without</th>
                <th className="py-1 text-right font-medium">Gap</th>
              </tr>
            </thead>
            <tbody>
              {p.comparisons.map((c) => (
                <tr key={c.label} className="border-t border-border">
                  <td translate="no" className="max-w-80 py-1.5 pr-3 [overflow-wrap:anywhere]">
                    {c.label}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">
                    {c.has} / {c.lacks}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{show(c.with)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{show(c.without)}</td>
                  <td className="py-1.5 text-right tabular-nums">
                    <span className={cn('inline-flex items-center gap-1', c.won ? 'text-success' : 'text-destructive')}>
                      {c.won ? <Check className="size-3" aria-hidden="true" /> : <X className="size-3" aria-hidden="true" />}
                      {signed(c.gap)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </li>
  );
}

// Part 4: patterns across ad sets. The verdict leads, with what chance alone would produce,
// so a list of near-misses can never read as a list of findings.
export function PairedPatternsView({ data, fmt }: { data: PairedPatterns; fmt: Formatters }) {
  const consistent = data.patterns.filter((p) => p.grade === 'consistent');
  const leaning = data.patterns.filter((p) => p.grade === 'leaning');
  return (
    <details className="group/pat rounded-xl border border-border">
      <summary className="flex cursor-pointer list-none items-start gap-2 px-4 py-3 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-open/pat:rotate-90" aria-hidden="true" />
        <span>
          <span id="patterns-title" className="block text-sm font-semibold">
            Across all ad sets: {consistent.length ? `${consistent.length} creative choices reliably win` : 'no creative choice reliably wins yet'}
          </span>
          <span className="block text-xs text-muted-foreground">
            {data.tested} tag and metric pairs tested across {data.adsets_used.video + data.adsets_used.image} ad sets
            {leaning.length > 0 && `; ${leaning.length} worth testing on purpose`}
          </span>
        </span>
      </summary>
      <div className="space-y-4 border-t border-border px-4 py-5">
        <div className="space-y-1">
          <p className="max-w-prose text-sm text-muted-foreground">
            Each ad set is its own test. For a tag, every ad set holding files with it and files without it is one comparison, on that ad set&apos;s own
            delivery. The question is how often the files with the tag did better. Uses {data.adsets_used.video} video and {data.adsets_used.image} image ad
            sets
            {data.duplicates.length > 0 && `; ${data.duplicates.length} ad sets holding exactly the same files as another were counted once`}. Hold is left out:
            it mostly measures video length.
          </p>
        </div>

        <div className={cn('rounded-xl border px-4 py-3', consistent.length ? 'border-success/40 bg-success/[0.06]' : 'border-border bg-muted/40')}>
          {consistent.length ? (
            <p className="text-sm font-medium">
              {consistent.length} of {data.tested} tag and metric pairs hold up after correcting for the number of tests.
            </p>
          ) : (
            <>
              <p className="text-sm font-medium">None of the {data.tested} tag and metric pairs holds up yet.</p>
              <p className="mt-1 max-w-prose text-sm text-muted-foreground">
                After correcting for testing {data.tested} pairs at once, no tag wins often enough to rule out chance. {leaning.length} lean one way, but a coin
                flip would make about {data.expected_by_chance} lean that far, so together they are no more than noise. Most ad sets hold 3 to 5 files; more
                tests of the same tags, side by side in one ad set, are what would settle them.
              </p>
            </>
          )}
        </div>

        {consistent.length > 0 && (
          <div className="space-y-1.5">
            <h3 className="text-sm font-semibold">Holds up</h3>
            <ul className="divide-y divide-border rounded-xl border border-border">
              {consistent.map((p) => (
                <Row key={`${p.format}${p.dimension}${p.value}${p.metric}`} p={p} fmt={fmt} />
              ))}
            </ul>
          </div>
        )}
        {leaning.length > 0 && (
          <div className="space-y-1.5">
            <h3 className="text-sm font-semibold">Leaning one way, not proven</h3>
            <p className="max-w-prose text-xs text-muted-foreground">
              Worth a deliberate test, not a decision. Several are one idea counted more than once: a Benadryl call-out, a Benadryl comparison and a
              Benadryl-swap concept are mostly the same files. Open a row to see each ad set.
            </p>
            <ul className="divide-y divide-border rounded-xl border border-border">
              {leaning.map((p) => (
                <Row key={`${p.format}${p.dimension}${p.value}${p.metric}`} p={p} fmt={fmt} />
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}
