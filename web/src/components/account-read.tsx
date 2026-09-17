'use client';

import { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from 'cn';
import type { AccountRead, AccountReadFinding } from '@/lib/types';
import { AccountReadAsk } from '@/components/account-read-ask';
import { EvidenceList, Mark, markClass } from '@/components/account-read-evidence';

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
                className="mr-0.5 font-mono text-[11px] text-primary transition-colors hover:text-foreground"
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

function Finding({ f, index, open, onToggle, tests, units }: {
  f: AccountReadFinding; index: number; open: boolean; onToggle: () => void;
  tests: AccountRead['tests'] extends infer T | undefined ? NonNullable<T> : never;
  units: NonNullable<AccountRead['units']>;
}) {
  const hasException = f.exception && !/^none( found)?\.?$/i.test(f.exception.trim());
  return (
    <article className="grid gap-x-8 gap-y-3 border-t border-border py-8 md:grid-cols-[5rem_1fr]">
      <div className="flex items-baseline gap-3 md:flex-col md:items-start md:gap-1.5">
        <span className="font-mono text-2xl font-medium tabular-nums leading-none text-muted-foreground/50">
          {String(index + 1).padStart(2, '0')}
        </span>
        <Mark className="text-muted-foreground">{f.layer}</Mark>
        <Mark className={markClass[f.confidence] ?? markClass.thin}>{f.confidence}</Mark>
      </div>
      <div className="min-w-0">
        <h3 className="max-w-[30ch] text-xl font-semibold leading-snug tracking-tight text-balance">{f.title}</h3>
        <p className="mt-3 max-w-[68ch] text-[15px] leading-7 text-pretty">
          <Cited text={f.paragraph} onOpen={onToggle} />
        </p>
        <p className="mt-4 max-w-[68ch] border-l-2 border-border pl-4 text-[15px] leading-7 text-muted-foreground">
          {f.mechanism}
        </p>
        <aside className="mt-4 max-w-[68ch] rounded-md bg-muted/60 px-4 py-3 text-[14px] leading-6">
          <Mark className="mr-2 text-muted-foreground">Not in the table</Mark>
          <span className="text-foreground">{f.why_non_obvious}</span>
        </aside>
        <dl className="mt-4 grid max-w-[68ch] gap-y-1 text-[13px] leading-6 text-muted-foreground sm:grid-cols-[auto_1fr] sm:gap-x-4">
          <dt className="font-medium text-foreground">Seen in</dt>
          <dd>{f.exemplars.join(' · ')}</dd>
          {hasException && (
            <>
              <dt className="font-medium text-foreground">Except</dt>
              <dd>{f.exception}</dd>
            </>
          )}
        </dl>
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="mt-4 inline-flex items-center gap-1 text-[13px] font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronRight className={cn('size-3.5 transition-transform duration-200', open && 'rotate-90')} aria-hidden="true" />
          Evidence · {f.pattern_ids.length}
        </button>
        {open && <EvidenceList ids={f.pattern_ids} tests={tests} units={units} />}
      </div>
    </article>
  );
}

function Note({ title, body, items, ordered }: { title: string; body?: string; items?: string[]; ordered?: boolean }) {
  const List = ordered ? 'ol' : 'ul';
  return (
    <section className="px-6 py-5">
      <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{title}</h4>
      {body && <p className="text-[14px] leading-6 text-pretty">{body}</p>}
      {items && (
        <List className={cn('space-y-2 pl-5 text-[14px] leading-6', ordered ? 'list-decimal marker:font-mono marker:text-muted-foreground' : 'list-disc marker:text-muted-foreground')}>
          {items.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </List>
      )}
    </section>
  );
}

export function AccountReadSection({ data, accountId }: { data: AccountRead; accountId: string }) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  if (data.status === 'none' || data.status === 'insufficient') {
    return (
      <section className="border-y border-border py-10 text-center text-sm text-muted-foreground">
        Account read needs 10 tagged video files. This account has {data.n_units ?? 0}.
      </section>
    );
  }
  const tests = data.tests ?? [];
  const units = data.units ?? [];
  const read = data.read;
  const s = (data.summary ?? {}) as Record<string, number>;
  const facts = [
    `${s.units} files`,
    `${s.creatives} creatives`,
    `$${Number(s.spend ?? 0).toLocaleString()} spend`,
    `${Number(s.purchases ?? 0).toLocaleString()} purchases`,
    data.window_end ? `through ${data.window_end}` : null,
  ].filter(Boolean);
  return (
    <section className="space-y-10">
      <header className="space-y-5">
        <Mark className="text-muted-foreground">Account read</Mark>
        {read ? (
          <p className="max-w-[62ch] text-lg leading-8 text-pretty md:text-[19px]">{read.overview}</p>
        ) : (
          <p className="max-w-[62ch] text-[15px] leading-7 text-muted-foreground">
            The prose read failed its checks on the last run{data.error ? `: ${data.error}` : ''}. The patterns are still current; ask a question below.
          </p>
        )}
        <p className="font-mono text-xs tabular-nums text-muted-foreground">
          {facts.map((f, i) => (
            <span key={i}>
              {i > 0 && <span className="mx-2 text-muted-foreground/40">/</span>}
              {f}
            </span>
          ))}
        </p>
        {data.newer_failed_error && (
          <p className="max-w-[68ch] text-[13px] leading-6 text-muted-foreground">
            A newer run failed its checks ({data.newer_failed_error.slice(0, 160)}). This is the last read that passed.
          </p>
        )}
      </header>

      <AccountReadAsk accountId={accountId} tests={tests} units={units} />

      {read && (
        <>
          <div>
            {read.findings.map((f, i) => (
              <Finding
                key={i}
                f={f}
                index={i}
                open={!!open[i]}
                onToggle={() => setOpen((o) => ({ ...o, [i]: !o[i] }))}
                tests={tests}
                units={units}
              />
            ))}
          </div>
          <div className="grid divide-y divide-border rounded-lg border border-border md:grid-cols-2 md:divide-x md:divide-y-0 [&>*:nth-child(n+3)]:md:border-t [&>*:nth-child(n+3)]:md:border-border">
            <Note title="Cannot be separated yet" body={read.entangled} />
            <Note title="No difference, or too few to test" body={read.no_evidence} />
            <Note title="Tests that follow" items={read.next_tests} ordered />
            <Note title="What would sharpen this" items={read.data_needed} />
          </div>
        </>
      )}
    </section>
  );
}
