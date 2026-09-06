'use client';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { ApiError, api } from '@/lib/api';

export function GenerateForm({ insightId, insights }: { insightId: string | null; insights: { id: string; label: string }[] }) {
  const router = useRouter();
  const [kind, setKind] = useState<'static' | 'video'>('static');
  const [variants, setVariants] = useState(4);
  const [insight, setInsight] = useState(insightId ?? '');
  const [concept, setConcept] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setMsg(null);
    try {
      const { id } = await api.createGeneration({ kind, variants, source_insight_id: insight || null, source_creative_ids: [], concept: concept.trim() || null });
      router.push(`/generate?created=${id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setMsg("You don't have permission to do this — ask an admin.");
      } else {
        setMsg(`Failed to start generation: ${err instanceof Error ? err.message : 'Something went wrong.'}`);
      }
    } finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} className="space-y-3 text-sm">
      <label className="block">Kind <select className="ml-1 rounded border p-1" value={kind} onChange={(e) => setKind(e.target.value as 'static' | 'video')}><option value="static">Static images</option><option value="video">Images + video</option></select></label>
      <label className="block">Variants <input type="number" min={1} max={8} className="ml-1 w-16 rounded border p-1" value={variants} onChange={(e) => setVariants(Number(e.target.value))} /></label>
      <label className="block">Source insight <select className="ml-1 rounded border p-1" value={insight} onChange={(e) => setInsight(e.target.value)}><option value="">None</option>{insights.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}</select></label>
      <label className="block">Concept<Textarea value={concept} onChange={(e) => setConcept(e.target.value)} placeholder="Describe the idea, audience, and offer" /></label>
      <Button type="submit" disabled={busy || (!insight && !concept.trim())}>Generate</Button>
      {msg && <p role="status" className="text-sm">{msg}</p>}
    </form>
  );
}
