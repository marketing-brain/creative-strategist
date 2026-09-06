'use client';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { api } from '@/lib/api';
import type { Conversation, Message } from '@/lib/types';

export function CopilotChat({ conversations, initialId }: { conversations: Conversation[]; initialId: string | null }) {
  const [convs, setConvs] = useState(conversations);
  const [active, setActive] = useState<string | null>(initialId);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState(false);
  const newConvRef = useRef<string | null>(null);

  useEffect(() => {
    if (active) {
      const isNew = active === newConvRef.current;
      api.messages(active).then((msgs) => {
        // Only update if not a newly created conversation
        if (!isNew) {
          setMessages(msgs);
        }
      });
      if (isNew) {
        newConvRef.current = null;
      }
    } else {
      setMessages([]);
    }
  }, [active]);

  const send = async () => {
    const content = input.trim();
    if (!content || pending) return;
    let id = active;
    if (!id) { const c = await api.newConversation(content.slice(0, 60)); id = c.id; newConvRef.current = id; setConvs([{ id, title: content.slice(0, 60), created_at: new Date().toISOString() }, ...convs]); setActive(id); }
    setMessages((m) => [...m, { role: 'user', content }]); setInput(''); setPending(true);
    try { const { reply } = await api.send(id, content); setMessages((m) => [...m, { role: 'assistant', content: reply }]); }
    finally { setPending(false); }
  };


  return (
    <div className="grid h-[70vh] grid-cols-4 gap-4">
      <aside className="space-y-2 overflow-y-auto border-r pr-3 text-sm">
        <Button size="sm" variant="outline" onClick={() => setActive(null)}>New conversation</Button>
        {convs.map((c) => <button key={c.id} onClick={() => setActive(c.id)} className={`block w-full truncate rounded px-2 py-1 text-left ${c.id === active ? 'bg-muted' : ''}`}>{c.title}</button>)}
      </aside>
      <section className="col-span-3 flex flex-col">
        <div className="flex-1 space-y-3 overflow-y-auto p-2">
          {messages.map((m, i) => <div key={i} className={`max-w-[80%] rounded p-3 text-sm ${m.role === 'user' ? 'ml-auto bg-primary text-primary-foreground' : 'bg-muted'}`}><pre className="whitespace-pre-wrap font-sans">{m.content}</pre></div>)}
          {pending && <div className="text-sm text-muted-foreground">Thinking…</div>}
        </div>
        <form className="flex gap-2 border-t pt-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about your creatives, e.g. which hooks have the lowest CPA this month?" />
          <Button type="submit" disabled={pending}>Send</Button>
        </form>
      </section>
    </div>
  );
}
