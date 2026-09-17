'use client';

import { useState } from 'react';
import { Sparkles } from 'lucide-react';
import type { AccountRead } from '@/lib/types';
import { AccountReadAsk } from '@/components/account-read-ask';
import { EvidenceList, Pill, gradeClass } from '@/components/account-read-evidence';

function Cited({ text, onOpen }: { text: string; onOpen: () => void }) {
  // "(P1, P2)" → superscript buttons that open the evidence list
  const parts = text.split(/(\((?:P\d+(?:,\s*)?)+\))/g);
  return (
    <>
      {parts.map((p, i) => {
        const ids = p.match(/P\d+/g);
        if (!ids || !/^\(/.test(p)) return <span key={i}>{p}</span>;
        return (
          <sup key={i} className="ml-0.5">
            {ids.map((id) => (
              <button
                key={id}
                type="button"
                onClick={onOpen}
                className="mr-0.5 font-mono text-[11px] text-primary hover:underline"
                aria-label={`pattern ${id}`}
              >
                {id.slice(1)}
              </button>
            ))}
          </sup>
        );
      })}
    </>
  );
}

function Block({ title, body, items }: { title: string; body?: string; items?: string[] }) {
  return (
    <div className="rounded-xl border border-border bg-card px-5 py-4">
      <h4 className="mb-1 text-sm font-semibold">{title}</h4>
      {body && <p className="text-sm leading-relaxed text-muted-foreground">{body}</p>}
      {items && (
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {items.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AccountReadSection({ data, accountId }: { data: AccountRead; accountId: string }) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  if (data.status === 'none' || data.status === 'insufficient') {
    return (
      <section className="rounded-xl border border-dashed border-border px-6 py-8 text-center text-sm text-muted-foreground">
        Account read needs 10 tagged video files. This account has {data.n_units ?? 0}.
      </section>
    );
  }
  const tests = data.tests ?? [];
  const units = data.units ?? [];
  const read = data.read;
  const s = (data.summary ?? {}) as Record<string, number>;
  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg font-semibold">Account read</h2>
        <span className="text-sm text-muted-foreground tabular-nums">
          {s.units} files · {s.creatives} creatives · ${Number(s.spend ?? 0).toLocaleString()} spend ·{' '}
          {Number(s.purchases ?? 0).toLocaleString()} purchases
          {data.window_end ? ` · through ${data.window_end}` : ''}
        </span>
      </div>
      <AccountReadAsk accountId={accountId} tests={tests} units={units} />
      {!read ? (
        <p className="text-sm text-muted-foreground">
          The prose read failed its checks on the last run{data.error ? `: ${data.error}` : ''}. The patterns are still current;
          ask a question above.
        </p>
      ) : (
        <>
          <div className="flex gap-3.5 rounded-xl border border-border bg-card px-6 py-4">
            <Sparkles className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <p className="text-base leading-relaxed text-pretty">{read.overview}</p>
          </div>
          <div className="space-y-6">
            {read.findings.map((f, i) => (
              <article key={i} className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Pill className="bg-primary/10 text-primary">{f.layer}</Pill>
                  <Pill className={gradeClass[f.confidence] ?? gradeClass.thin}>{f.confidence}</Pill>
                </div>
                <h3 className="text-base font-semibold">{f.title}</h3>
                <p className="text-sm leading-relaxed text-pretty">
                  <Cited text={f.paragraph} onOpen={() => setOpen((o) => ({ ...o, [i]: true }))} />
                </p>
                <dl className="grid gap-1 text-sm text-muted-foreground sm:grid-cols-[auto_1fr] sm:gap-x-4">
                  <dt className="font-medium text-foreground">Why</dt>
                  <dd>{f.mechanism}</dd>
                  <dt className="font-medium text-foreground">See</dt>
                  <dd>{f.exemplars.join(' · ')}</dd>
                  {f.exception && f.exception !== 'none found' && (
                    <>
                      <dt className="font-medium text-foreground">Except</dt>
                      <dd>{f.exception}</dd>
                    </>
                  )}
                  <dt className="font-medium text-foreground">Not in the table</dt>
                  <dd>{f.why_non_obvious}</dd>
                </dl>
                <button
                  type="button"
                  onClick={() => setOpen((o) => ({ ...o, [i]: !o[i] }))}
                  className="text-xs font-medium text-muted-foreground hover:text-foreground"
                  aria-expanded={!!open[i]}
                >
                  {open[i] ? '▾' : '▸'} Evidence · {f.pattern_ids.length}
                </button>
                {open[i] && <EvidenceList ids={f.pattern_ids} tests={tests} units={units} />}
              </article>
            ))}
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Block title="Cannot be separated yet" body={read.entangled} />
            <Block title="No difference, or too few to test" body={read.no_evidence} />
            <Block title="Tests that follow" items={read.next_tests} />
            <Block title="What would sharpen this" items={read.data_needed} />
          </div>
        </>
      )}
    </section>
  );
}
