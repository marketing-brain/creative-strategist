'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import { EvidenceList, Pill } from '@/components/account-read-evidence';
import type { AccountReadAnswer, AccountReadTest, AccountReadUnit } from '@/lib/types';

const verdictClass: Record<string, string> = {
  answerable: 'bg-success/10 text-success',
  directional: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  'not yet': 'bg-muted text-muted-foreground',
};

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
    <div className="space-y-3 rounded-xl border border-border bg-card px-5 py-4">
      <form onSubmit={submit} className="flex flex-wrap gap-2">
        <label htmlFor="account-read-question" className="sr-only">
          Ask about this account
        </label>
        <input
          id="account-read-question"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask, e.g. which messenger works for people who already take magnesium?"
          className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={pending || !q.trim()}
          className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {pending ? 'Thinking…' : 'Ask'}
        </button>
      </form>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {answer?.unmappable && <p className="text-sm text-muted-foreground">{answer.unmappable}</p>}
      {answer && !answer.unmappable && (
        <div className="space-y-2">
          {answer.verdict && <Pill className={verdictClass[answer.verdict] ?? verdictClass['not yet']}>{answer.verdict}</Pill>}
          <p className="text-sm leading-relaxed text-pretty">{answer.answer}</p>
          {answer.what_would_settle_it && <p className="text-xs text-muted-foreground">To settle it: {answer.what_would_settle_it}</p>}
          {(answer.pattern_ids?.length ?? 0) > 0 && (
            <EvidenceList ids={answer.pattern_ids ?? []} tests={answerTests.length ? answerTests : tests} units={units} />
          )}
        </div>
      )}
    </div>
  );
}
