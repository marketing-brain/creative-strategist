'use client';

import { useEffect, useRef, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { cn } from 'cn';
import { api } from '@/lib/api';
import { EvidenceList, Mark, markClass } from '@/components/account-read-evidence';
import type { AccountReadAnswer, AccountReadTest, AccountReadUnit } from '@/lib/types';

type Entry = { id: number; question: string; answer?: AccountReadAnswer; error?: string };

// One seeded question per query kind, as the empty-state menu.
const SEEDS = [
  'Where is our money going versus where the evidence points?',
  'Which proven ingredients have we never combined?',
  'What separates our best CPA video from the ads most like it that failed?',
  'Is the expert effect real, or just where we spent the money?',
  'Which single ingredient is doing the work in our best recipe?',
];

export function AccountReadAsk({ accountId, tests, units }: { accountId: string; tests: AccountReadTest[]; units: AccountReadUnit[] }) {
  const [q, setQ] = useState('');
  const [entries, setEntries] = useState<Entry[]>([]);
  const [pending, setPending] = useState(false);
  const nextId = useRef(0);
  const endRef = useRef<HTMLDivElement>(null);

  // An answer takes ~15s, so the newest entry is often below the fold by the time it lands.
  useEffect(() => {
    if (entries.length) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [entries]);

  const ask = async (question: string) => {
    if (!question || pending) return;
    const id = nextId.current++;
    setEntries((prev) => [...prev, { id, question }]);
    setQ('');
    setPending(true);
    try {
      const answer = await api.accountReadAsk(accountId, question);
      setEntries((prev) => prev.map((x) => (x.id === id ? { ...x, answer } : x)));
    } catch (err) {
      // Attached to its own entry, never page-level: one failed question must not
      // wipe the answers above it.
      const error = err instanceof Error ? err.message : 'The question could not be answered.';
      setEntries((prev) => prev.map((x) => (x.id === id ? { ...x, error } : x)));
    } finally {
      setPending(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    void ask(q.trim());
  };

  return (
    <div className="space-y-8">
      {entries.map((entry) => (
        <Exchange key={entry.id} entry={entry} tests={tests} units={units} />
      ))}
      <div ref={endRef} />
      <form onSubmit={submit} className="group flex items-center gap-3 border-b border-border py-1 transition-colors focus-within:border-foreground">
        <label htmlFor="account-read-question" className="sr-only">
          Ask this account a question
        </label>
        <input
          id="account-read-question"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={
            entries.length
              ? 'Ask another…'
              : 'Ask this account a question, e.g. which messenger works for people who already take magnesium?'
          }
          className="min-w-0 flex-1 bg-transparent py-2 text-[15px] text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
        />
        <button
          type="submit"
          disabled={pending || !q.trim()}
          aria-label="Ask"
          className={cn(
            'flex size-8 shrink-0 items-center justify-center rounded-full text-primary-foreground transition-all',
            'bg-primary hover:bg-primary/90 active:scale-95 disabled:bg-muted disabled:text-muted-foreground',
            pending && 'animate-pulse'
          )}
        >
          <ArrowRight className="size-4" aria-hidden="true" />
        </button>
      </form>
      {entries.length === 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="Example questions">
          {SEEDS.map((s) => (
            <li key={s}>
              <button
                type="button"
                onClick={() => void ask(s)}
                disabled={pending}
                className="rounded-full border border-border px-3 py-1 text-left text-[13px] leading-6 text-muted-foreground transition-colors hover:border-foreground hover:text-foreground disabled:opacity-50"
              >
                {s}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Exchange({ entry, tests, units }: { entry: Entry; tests: AccountReadTest[]; units: AccountReadUnit[] }) {
  const { question, answer, error } = entry;
  const answerTests = answer?.tests ?? [];
  const settled = Boolean(answer || error);
  return (
    <div className="space-y-3">
      <p className="max-w-[68ch] text-[15px] leading-7 font-medium text-pretty">{question}</p>
      {answer?.read_as && <p className="text-[13px] leading-6 text-muted-foreground">Read as: {answer.read_as}</p>}
      {!settled && <p className="text-sm text-muted-foreground">Reading the account…</p>}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {answer?.unmappable && <p className="text-sm text-muted-foreground">{answer.unmappable}</p>}
      {answer && !answer.unmappable && (
        <div className="rounded-lg bg-muted/60 px-5 py-4">
          {answer.verdict && <Mark className={markClass[answer.verdict] ?? markClass['not yet']}>{answer.verdict}</Mark>}
          <p className="mt-1.5 max-w-[68ch] text-[15px] leading-7 text-pretty">{answer.answer}</p>
          {answer.what_would_settle_it && (
            <p className="mt-2 max-w-[68ch] text-[13px] leading-6 text-muted-foreground">To settle it: {answer.what_would_settle_it}</p>
          )}
          {(answer.facts?.length ?? 0) > 0 && (
            <ol className="mt-3 space-y-1 text-[13px] leading-6">
              {answer.facts!.map((f) => (
                <li key={f.id} className="grid grid-cols-[2.25rem_1fr] gap-x-2">
                  <span className="pt-px font-mono text-xs tabular-nums text-muted-foreground/60">{f.id}</span>
                  <span className="text-foreground">{f.text}</span>
                </li>
              ))}
            </ol>
          )}
          {(answer.pattern_ids?.length ?? 0) > 0 && (
            <EvidenceList ids={answer.pattern_ids ?? []} tests={answerTests.length ? answerTests : tests} units={units} />
          )}
        </div>
      )}
    </div>
  );
}
