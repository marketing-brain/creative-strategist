'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowUp, Check, Copy, PenLine, Sparkles } from 'lucide-react';
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

  const send = () => void ask(q.trim());
  const empty = entries.length === 0;

  // One tree for both states: the composer keeps its DOM node -- and the caret in it --
  // when the first question turns the centred empty state into a transcript. Only the
  // classes around it move.
  //
  // Absolute inset-0 is what pins the pane to the height of <main>: it takes the frame
  // out of flow, so a long transcript can never push the page itself taller than the
  // viewport and leave the composer below the fold. The wrapper in page.tsx is relative.
  return (
    <div className="absolute inset-0 flex flex-col">
      <header className="flex shrink-0 items-center justify-between gap-3 pb-2">
        <h1 className="text-sm font-semibold tracking-tight">Ask</h1>
        {!empty && (
          <button
            type="button"
            onClick={() => {
              setEntries([]);
              setQ('');
            }}
            className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-[13px] text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
          >
            <PenLine className="size-3.5" aria-hidden="true" />
            New question
          </button>
        )}
      </header>

      {/* Transcript, composer and footer. Empty, the pane centres them; once a question
          has been asked the transcript takes the slack and the composer sits at the foot. */}
      <div className={cn('flex min-h-0 flex-1 flex-col', empty && 'justify-center pb-10')}>
        <div className={cn(empty ? 'shrink-0 pb-7' : '-mx-1 min-h-0 flex-1 overflow-y-auto px-1')}>
          {empty ? (
            <div className="flex flex-col items-center gap-3 text-center">
              <span className="flex size-10 items-center justify-center rounded-2xl border border-border bg-card shadow-sm">
                <Sparkles className="size-5 text-primary" aria-hidden="true" />
              </span>
              <h2 className="text-2xl font-semibold tracking-tight text-pretty">What do you want to know?</h2>
              <p className="max-w-md text-[13px] leading-6 text-muted-foreground text-pretty">
                Put a question to this account&apos;s creative. Answers come from graded comparisons, with the evidence attached.
              </p>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-8 pt-2 pb-8">
              {entries.map((entry) => (
                <Exchange key={entry.id} entry={entry} tests={tests} units={units} />
              ))}
              <div ref={endRef} />
            </div>
          )}
        </div>

        <div className={cn('mx-auto w-full shrink-0 bg-background', empty ? 'max-w-2xl' : 'max-w-3xl pt-2')}>
          <Composer value={q} onChange={setQ} onSend={send} pending={pending} started={!empty} />
        </div>

        <div className={cn('mx-auto w-full shrink-0', empty ? 'max-w-2xl pt-3' : 'max-w-3xl')}>
          {empty ? (
            <ul className="grid gap-2 sm:grid-cols-2" aria-label="Example questions">
              {SEEDS.map((s) => (
                <li key={s} className="sm:last:col-span-2">
                  <button
                    type="button"
                    onClick={() => void ask(s)}
                    disabled={pending}
                    className="flex h-full w-full items-start rounded-xl border border-border bg-card/60 px-3.5 py-2.5 text-left text-[13px] leading-6 text-muted-foreground transition-colors hover:border-foreground/20 hover:bg-muted/60 hover:text-foreground disabled:opacity-50"
                  >
                    {s}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="pt-2 text-center text-[11px] text-muted-foreground">
              Answers look back at what already ran. No forecasts, no predicted ROAS.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function Composer({
  value,
  onChange,
  onSend,
  pending,
  started,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  pending: boolean;
  started: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with the question up to a few lines, then scroll inside the box.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSend();
      }}
      className="flex items-end gap-2 rounded-[1.75rem] border border-border bg-card px-2.5 py-2 shadow-sm transition-[border-color,box-shadow] focus-within:border-foreground/25 focus-within:shadow-md"
    >
      <label htmlFor="account-read-question" className="sr-only">
        Ask this account a question
      </label>
      <textarea
        id="account-read-question"
        ref={ref}
        rows={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          // Enter sends, Shift+Enter breaks the line -- the shape everyone already knows.
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            onSend();
          }
        }}
        placeholder={started ? 'Ask a follow-up…' : 'Ask about a hook, a messenger, a claim, a format…'}
        className="max-h-[200px] min-w-0 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-7 text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
      />
      <button
        type="submit"
        disabled={pending || !value.trim()}
        aria-label="Ask"
        className={cn(
          'mb-0.5 flex size-8 shrink-0 items-center justify-center rounded-full text-primary-foreground transition-all',
          'bg-primary hover:bg-primary/90 active:scale-95 disabled:bg-muted disabled:text-muted-foreground'
        )}
      >
        {pending ? (
          <span className="size-2.5 animate-pulse rounded-[3px] bg-current" aria-hidden="true" />
        ) : (
          <ArrowUp className="size-4" aria-hidden="true" />
        )}
      </button>
    </form>
  );
}

function Exchange({ entry, tests, units }: { entry: Entry; tests: AccountReadTest[]; units: AccountReadUnit[] }) {
  const { question, answer, error } = entry;
  const answerTests = answer?.tests ?? [];
  const settled = Boolean(answer || error);
  return (
    <div className="space-y-5">
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-3xl rounded-br-lg bg-muted px-4 py-2.5 text-[15px] leading-7 whitespace-pre-wrap text-pretty">{question}</p>
      </div>
      <div className="flex gap-3">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-card">
          <Sparkles className="size-3.5 text-primary" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1 space-y-2.5">
          {answer?.read_as && <p className="text-[13px] leading-6 text-muted-foreground">Read as: {answer.read_as}</p>}
          {!settled && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
              <Dots />
              Reading the account…
            </p>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          {answer?.unmappable && <p className="max-w-[68ch] text-[15px] leading-7 text-muted-foreground text-pretty">{answer.unmappable}</p>}
          {answer && !answer.unmappable && (
            <div>
              {answer.verdict && <Mark className={markClass[answer.verdict] ?? markClass['not yet']}>{answer.verdict}</Mark>}
              <p className="mt-1.5 max-w-[68ch] text-[15px] leading-7 whitespace-pre-wrap text-pretty">{answer.answer}</p>
              {answer.what_would_settle_it && (
                <p className="mt-2 max-w-[68ch] text-[13px] leading-6 text-muted-foreground">To settle it: {answer.what_would_settle_it}</p>
              )}
              {(answer.facts?.length ?? 0) > 0 && (
                <ol className="mt-3 space-y-1 rounded-lg bg-muted/60 px-4 py-3 text-[13px] leading-6">
                  {answer.facts!.map((f) => (
                    <li key={f.id} className="grid grid-cols-[2.25rem_1fr] gap-x-2">
                      <span className="pt-px font-mono text-xs tabular-nums text-muted-foreground/60">{f.id}</span>
                      <span className="text-foreground">{f.text}</span>
                    </li>
                  ))}
                </ol>
              )}
              {/* F-ids are already rendered above as facts; only P-ids have an evidence row. */}
              {(answer.pattern_ids ?? []).some((id) => id.startsWith('P')) && (
                <EvidenceList ids={answer.pattern_ids ?? []} tests={answerTests.length ? answerTests : tests} units={units} />
              )}
              {answer.answer && <CopyButton text={answer.answer} />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Dots() {
  return (
    <span className="flex gap-1" aria-hidden="true">
      {[0, 150, 300].map((d) => (
        <span key={d} className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60" style={{ animationDelay: `${d}ms` }} />
      ))}
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <button
      type="button"
      aria-label={copied ? 'Copied' : 'Copy answer'}
      onClick={() => void navigator.clipboard?.writeText(text).then(() => setCopied(true))}
      className="mt-2 flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      {copied ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}
