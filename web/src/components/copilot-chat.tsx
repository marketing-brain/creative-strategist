'use client';
import { useEffect, useRef, useState } from 'react';
import { MessageSquarePlus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from 'cn';
import { ApiError, api } from '@/lib/api';
import type { Conversation, Message } from '@/lib/types';

export function CopilotChat({ conversations, initialId }: { conversations: Conversation[]; initialId: string | null }) {
  const [convs, setConvs] = useState(conversations);
  const [active, setActive] = useState<string | null>(initialId);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const newConvRef = useRef<string | null>(null);

  useEffect(() => {
    if (active) {
      const isNew = active === newConvRef.current;
      setLoadError(null);
      api
        .messages(active)
        .then((msgs) => {
          // Only update if not a newly created conversation
          if (!isNew) {
            setMessages(msgs);
          }
        })
        .catch((e) => {
          if (!isNew) setMessages([]);
          setLoadError(
            e instanceof ApiError && e.status === 403
              ? "You don't have permission to read this conversation — ask an admin."
              : `Couldn't load this conversation: ${e instanceof Error ? e.message : 'Something went wrong.'}`
          );
        });
      if (isNew) {
        newConvRef.current = null;
      }
    } else {
      setMessages([]);
      setLoadError(null);
    }
  }, [active]);

  const send = async () => {
    const content = input.trim();
    if (!content || pending) return;
    let id = active;
    try {
      if (!id) {
        const c = await api.newConversation(content.slice(0, 60));
        id = c.id;
        newConvRef.current = id;
        setConvs([{ id, title: content.slice(0, 60), created_at: new Date().toISOString() }, ...convs]);
        setActive(id);
      }
      setMessages((m) => [...m, { role: 'user', content }]);
      setInput('');
      setPending(true);
      const { reply } = await api.send(id, content);
      setMessages((m) => [...m, { role: 'assistant', content: reply }]);
    } catch {
      setMessages((m) => [...m, { role: 'assistant', content: 'Something went wrong sending that message. Please try again.' }]);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 md:grid-cols-4">
      <aside className="flex flex-col gap-1 overflow-y-auto border-b border-border pb-3 md:border-r md:border-b-0 md:pr-3 md:pb-0">
        <Button size="sm" variant="outline" className="mb-1 justify-start gap-1.5" onClick={() => setActive(null)}>
          <MessageSquarePlus className="size-4" aria-hidden="true" />
          New conversation
        </Button>
        {convs.map((c) => (
          <button
            key={c.id}
            onClick={() => setActive(c.id)}
            aria-current={c.id === active ? 'true' : undefined}
            className={cn(
              'block w-full truncate rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors',
              c.id === active ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            )}
          >
            {`${c.title} · ${new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`}
          </button>
        ))}
      </aside>
      <section className="flex min-h-[60vh] flex-col md:col-span-3">
        <div className="flex-1 space-y-3 overflow-y-auto p-1" aria-live="polite">
          {messages.length === 0 && !pending && (
            <p className="py-10 text-center text-sm text-muted-foreground">Ask about hooks, formats, or which creatives are working.</p>
          )}
          {messages.map((m, i) => (
            <div
              key={i}
              className={cn(
                'max-w-[80%] rounded-xl px-3 py-2 text-sm leading-relaxed',
                m.role === 'user' ? 'ml-auto bg-primary text-primary-foreground' : 'bg-muted text-foreground'
              )}
            >
              <pre className="font-sans whitespace-pre-wrap">{m.content}</pre>
            </div>
          ))}
          {pending && <div className="text-sm text-muted-foreground">Thinking…</div>}
          {loadError && (
            <p role="status" className="text-sm text-destructive">
              {loadError}
            </p>
          )}
        </div>
        <form className="flex gap-2 border-t border-border pt-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your creatives, e.g. which hooks have the lowest CPA this month…"
            aria-label="Message"
          />
          <Button type="submit" disabled={pending || !input.trim()}>
            Send
          </Button>
        </form>
      </section>
    </div>
  );
}
