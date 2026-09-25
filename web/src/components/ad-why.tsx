import { ChevronRight } from 'lucide-react';
import { cn } from 'cn';
import { label, words } from '@/components/tag-list';
import type { Formatters } from '@/lib/format';
import type { AdSetRow, WhyStep, WhyStepKey } from '@/lib/types';

const signed = (x: number) => `${x >= 0 ? '+' : '−'}${Math.round(Math.abs(x) * 100)}%`;
const pct = (x: number | null | undefined) => (x == null ? '–' : `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%`);

// One step of cost per purchase: this ad's value, the siblings', and how far it pushed the cost.
// The bar grows left for a step that made the ad cheaper, right for one that made it dearer,
// on a log scale so +100% and −50% (the same size of effect) draw the same length.
function StepRow({ s, fmt, main }: { s: WhyStep; fmt: Formatters; main: boolean }) {
  const show = (x: number | null) => (x == null ? '–' : s.key === 'cpm' ? fmt.money(x) : pct(x));
  const size = s.push == null ? 0 : Math.min(1, Math.abs(Math.log(1 + s.push)) / Math.log(3));
  const good = s.push != null && s.push < 0;
  return (
    <div
      className={cn(
        'grid grid-cols-[minmax(0,11rem)_minmax(0,9rem)_minmax(0,1fr)_4rem] items-center gap-3 rounded-md px-2 py-1.5 text-xs',
        main && 'bg-muted/60'
      )}
    >
      <span className={cn(main && 'font-semibold')}>{s.label}</span>
      <span className="text-muted-foreground tabular-nums" title={s.low != null ? `95% range ${pct(s.low)} – ${pct(s.high)}` : undefined}>
        <span className="font-medium text-foreground">{show(s.mine)}</span> vs {show(s.theirs)}
      </span>
      <span className="relative h-2.5" aria-hidden="true">
        <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
        {s.push != null && (
          <span
            className={cn('absolute inset-y-0 rounded-full', !s.clear ? 'bg-muted-foreground/25' : good ? 'bg-success/70' : 'bg-destructive/70')}
            style={good ? { right: '50%', width: `${size * 50}%` } : { left: '50%', width: `${size * 50}%` }}
          />
        )}
      </span>
      <span className={cn('text-right tabular-nums', !s.clear ? 'text-muted-foreground' : good ? 'text-success' : 'text-destructive')}>
        {s.push == null ? '–' : signed(s.push)}
        {s.push != null && !s.clear && <span className="block text-[10px] text-muted-foreground">not clear</span>}
      </span>
    </div>
  );
}

function Opening({ o }: { o: NonNullable<AdSetRow['why']['opening']> }) {
  return (
    <div className="space-y-1 text-sm">
      {o.words && <p>&ldquo;{o.words}&rdquo;</p>}
      {o.text && o.text.length > 0 && <p className="text-xs text-muted-foreground">On screen in the first 3 seconds: {o.text.join(' / ')}</p>}
      <p className="flex flex-wrap gap-1.5 text-[11px]">
        {o.face != null && <span className="rounded bg-muted px-1.5 py-0.5">{o.face ? 'A face in the opening' : 'No face in the opening'}</span>}
        {o.product != null && <span className="rounded bg-muted px-1.5 py-0.5">{o.product ? 'Product in the opening' : 'No product in the opening'}</span>}
      </p>
    </div>
  );
}

function StepEvidence({ step, row, rows, fmt }: { step: WhyStepKey; row: AdSetRow; rows: AdSetRow[]; fmt: Formatters }) {
  const w = row.why;
  // Watch-through is a share of each video's own length, so a 111 s video and 10 s ones are not
  // comparable point for point: say so when lengths are more than 2x apart.
  const others = rows.filter((x) => x.id !== row.id && x.duration_s != null).map((x) => x.duration_s!);
  const median = others.length ? [...others].sort((a, b) => a - b)[Math.floor(others.length / 2)] : null;
  const lengthGap = row.duration_s != null && median != null && median > 0 && (row.duration_s / median > 2 || median / row.duration_s > 2);
  const n = w.siblings;
  if (step === 'stop') {
    return (
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <h5 className="text-xs font-medium text-muted-foreground">This ad&apos;s first 3 seconds</h5>
          {w.opening ? <Opening o={w.opening} /> : <p className="text-xs text-muted-foreground">No opening recorded for this file.</p>}
        </div>
        <div className="space-y-1.5">
          <h5 className="text-xs font-medium text-muted-foreground">The other ads&apos; openings, best stop rate first</h5>
          <ul className="space-y-2">
            {w.sibling_openings.slice(0, 4).map((o) => (
              <li key={o.id} className="text-xs">
                <span className="font-medium tabular-nums">{pct(o.stop_rate)}</span> <span className="text-muted-foreground">stop</span>
                {o.words ? (
                  <span> · &ldquo;{o.words.length > 140 ? `${o.words.slice(0, 140)}…` : o.words}&rdquo;</span>
                ) : (
                  <span className="text-muted-foreground"> · no opening recorded</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    );
  }
  if (step === 'cpm') {
    return (
      <div className="space-y-1.5">
        <h5 className="text-xs font-medium text-muted-foreground">Who Meta showed it to, against the other ads here</h5>
        {w.audience.length ? (
          <table className="text-xs">
            <tbody>
              {w.audience.map((a) => (
                <tr key={a.segment}>
                  <td className="py-0.5 pr-4 capitalize">{a.segment}</td>
                  <td className="py-0.5 pr-4 tabular-nums">
                    <span className="font-medium">{pct(a.share)}</span> of its impressions vs {pct(a.siblings_share)}
                  </td>
                  <td className="py-0.5 text-muted-foreground tabular-nums">{a.cpm != null ? `${fmt.money(a.cpm)} per 1,000 here` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-xs text-muted-foreground">No audience breakdown for this ad.</p>
        )}
        <p className="max-w-prose text-xs text-muted-foreground">
          Meta chooses who sees each ad, and prices an ad lower when people engage with it, so a cheaper cost to reach people is partly the creative and partly
          where Meta sent it.
        </p>
      </div>
    );
  }
  const tags = w.step_tags[step] ?? [];
  const ret = row.retention;
  return (
    <div className="space-y-2">
      {tags.length > 0 && (
        <div className="space-y-1">
          <h5 className="text-xs font-medium text-muted-foreground">
            {step === 'buy' ? 'What it promises' : 'What it does after the opening'} (how many of the {n} other ads share it)
          </h5>
          <ul className="flex flex-wrap gap-1.5">
            {tags.map((t) => (
              <li
                key={t.dimension + t.value}
                className={cn('rounded-md px-2 py-0.5 text-xs', t.siblings_with === 0 ? 'bg-chart-1/10' : 'bg-muted')}
                title={t.unreliable ? 'The tagger is often unsure about this kind of tag' : undefined}
              >
                <span className="text-muted-foreground">{label(t.dimension)}:</span> {words(t.value)}{' '}
                <span className="text-muted-foreground tabular-nums">
                  {t.siblings_with}/{n}
                </span>
                {t.unreliable && <span className="text-muted-foreground"> ?</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {step !== 'buy' && ret && w.sibling_retention && (
        <p className="text-xs text-muted-foreground tabular-nums">
          Still watching: {ret.map((p, i) => `${p.label} ${pct(p.share)} (others ${pct(w.sibling_retention![i]?.share)})`).join(' · ')}
          {lengthGap && (
            <span className="mt-0.5 block text-foreground">
              It runs {Math.round(row.duration_s!)}s against the others&apos; typical {Math.round(median!)}s, so each point is much further into the video;
              compare with care.
            </span>
          )}
        </p>
      )}
      {w.described && (step === 'buy' ? w.described.funnel_stage : w.described.angle) && (
        <p className="max-w-prose text-xs">
          <span className="text-muted-foreground">The model&apos;s description (its words, not a measurement): </span>
          {step === 'buy' ? w.described.funnel_stage : w.described.angle}
        </p>
      )}
      {tags.length === 0 && !ret && !w.described && <p className="text-xs text-muted-foreground">No facts recorded for this step.</p>}
    </div>
  );
}

const EVIDENCE_TITLE: Record<WhyStepKey, string> = {
  cpm: 'Why it cost what it did to reach people',
  stop: 'Why people did or didn’t stop',
  click: 'Why those who stopped did or didn’t click',
  ctr: 'Why people did or didn’t click',
  buy: 'Why clickers did or didn’t buy',
};

export function AdWhy({ rows, fmt, noun, group = 'ad set' }: { rows: AdSetRow[]; fmt: Formatters; noun: string; group?: string }) {
  const withWhy = rows.filter((r) => r.why.decomposition);
  if (!withWhy.length) return null;
  return (
    <div className="space-y-3">
      <h3 className="font-semibold">Why each of the {noun} did what it did</h3>
      <p className="max-w-prose text-sm text-muted-foreground">
        Cost per purchase is the cost to reach people, divided by how many stop, how many of those click, and how many of those buy. Each bar is how far one of
        those steps moved this ad&apos;s cost against the rest of the {group}: left made it cheaper, right made it dearer. Grey bars are swings too small to
        tell from noise. Below the bars are the facts in the ad at the steps that mattered, beside the other ads&apos; versions.
      </p>
      <ul className="space-y-2">
        {withWhy.map((r, i) => {
          const w = r.why;
          const d = w.decomposition!;
          const shown = d.steps.filter((s) => s.clear && s.push != null && Math.abs(s.push) >= 0.1).sort((a, b) => Math.abs(b.push!) - Math.abs(a.push!));
          return (
            <li key={r.id} className="rounded-xl border border-border">
              <details className="group" open={i === 0}>
                <summary className="flex cursor-pointer list-none items-start gap-3 px-4 py-3 [&::-webkit-details-marker]:hidden">
                  <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
                  <span className="min-w-0 space-y-0.5">
                    <span translate="no" className="block text-sm font-medium [overflow-wrap:anywhere]">
                      {r.name ?? 'Untitled'}
                    </span>
                    <span className="block text-sm">{d.summary}</span>
                  </span>
                </summary>
                <div className="space-y-4 px-4 pb-4 pl-11">
                  <div className="space-y-0.5">
                    {d.steps.map((s) => (
                      <StepRow key={s.key} s={s} fmt={fmt} main={s.key === w.main_step} />
                    ))}
                    {d.aov != null && d.siblings_aov != null && (
                      <p className="px-2 pt-1 text-xs text-muted-foreground tabular-nums">
                        Order value {fmt.money(d.aov)} vs {fmt.money(d.siblings_aov)}: it affects ROAS, not cost per purchase.
                      </p>
                    )}
                  </div>
                  {shown.map((s) => (
                    <div key={s.key} className="space-y-2 border-t border-border pt-3">
                      <h4 className="text-sm font-medium">{EVIDENCE_TITLE[s.key]}</h4>
                      <StepEvidence step={s.key} row={r} rows={rows} fmt={fmt} />
                    </div>
                  ))}
                  <p className="border-t border-border pt-3 text-xs text-muted-foreground">
                    It took {w.spend_share != null ? `${Math.round(w.spend_share * 100)}%` : '?'} of the {group}&apos;s spend over {r.days} day
                    {r.days === 1 ? '' : 's'}. Meta gives better ads more budget and better audiences, so part of any gap is that allocation. This is one ad
                    against {w.siblings} others: a lead to test, not proof.
                  </p>
                </div>
              </details>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
