'use client';

import { useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { cn } from 'cn';
import { api } from '@/lib/api';
import { EvidenceList, Mark, markClass } from '@/components/account-read-evidence';
import type { AccountReadAnswer, AccountReadTest, AccountReadUnit } from '@/lib/types';

export function AccountReadAsk({ accountId, tests, units }: { accountId: string; tests: AccountReadTest[]; units: AccountReadUnit[] }) {
  const [q, setQ] = useState('');
  const [answer, setAnswer] = useState<AccountReadAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!q.trim() || pending) return;
    setError(null);
    setPending(true);
    try {
      setAnswer(await api.accountReadAsk(accountId, q.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The question could not be answered.');
    } finally {
      setPending(false);
    }
  };
  const answerTests = answer?.tests ?? [];
  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="group flex items-center gap-3 border-b border-border py-1 transition-colors focus-within:border-foreground">
        <label htmlFor="account-read-question" className="sr-only">
          Ask this account a question
        </label>
        <input
          id="account-read-question"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask this account a question, e.g. which messenger works for people who already take magnesium?"
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
      {pending && <p className="text-sm text-muted-foreground">Reading the account…</p>}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {answer?.unmappable && <p className="text-sm text-muted-foreground">{answer.unmappable}</p>}
      {answer && !answer.unmappable && (
        <div className="rounded-lg bg-muted/60 px-5 py-4">
          {answer.verdict && <Mark className={markClass[answer.verdict] ?? markClass['not yet']}>{answer.verdict}</Mark>}
          <p className="mt-1.5 max-w-[68ch] text-[15px] leading-7 text-pretty">{answer.answer}</p>
          {answer.what_would_settle_it && (
            <p className="mt-2 max-w-[68ch] text-[13px] leading-6 text-muted-foreground">To settle it: {answer.what_would_settle_it}</p>
          )}
          {(answer.pattern_ids?.length ?? 0) > 0 && (
            <EvidenceList ids={answer.pattern_ids ?? []} tests={answerTests.length ? answerTests : tests} units={units} />
          )}
        </div>
      )}
    </div>
  );
}
