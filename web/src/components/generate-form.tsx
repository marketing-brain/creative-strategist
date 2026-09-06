'use client';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
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
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const { id } = await api.createGeneration({ kind, variants, source_insight_id: insight || null, source_creative_ids: [], concept: concept.trim() || null });
      router.push(`/generate?created=${id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setMsg("You don't have permission to do this — ask an admin.");
      } else {
        setMsg(`Failed to start generation: ${err instanceof Error ? err.message : 'Something went wrong.'}`);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          Kind
          <Select value={kind} onValueChange={(v) => setKind(v as 'static' | 'video')}>
            <SelectTrigger aria-label="Kind">
              <SelectValue>{(v: string) => (v === 'video' ? 'Images + video' : 'Static images')}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="static">Static images</SelectItem>
              <SelectItem value="video">Images + video</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          Variants
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={8}
            className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm tabular-nums outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            value={variants}
            onChange={(e) => setVariants(Number(e.target.value))}
          />
        </label>
      </div>
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        Source insight
        <Select value={insight} onValueChange={(v) => setInsight(v ?? '')}>
          <SelectTrigger aria-label="Source insight" className="w-full">
            <SelectValue placeholder="None">{(v: string) => insights.find((i) => i.id === v)?.label ?? 'None'}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">None</SelectItem>
            {insights.map((i) => (
              <SelectItem key={i.id} value={i.id}>
                {i.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        Concept
        <Textarea value={concept} onChange={(e) => setConcept(e.target.value)} placeholder="Describe the idea, audience, and offer…" />
      </label>
      <Button type="submit" disabled={busy || (!insight && !concept.trim())}>
        {busy ? 'Starting…' : 'Generate'}
      </Button>
      {msg && (
        <p role="status" className="text-sm text-destructive">
          {msg}
        </p>
      )}
    </form>
  );
}
