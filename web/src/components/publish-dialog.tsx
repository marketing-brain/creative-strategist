'use client';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { api } from '@/lib/api';
import type { GeneratedAsset } from '@/lib/types';

type Opt = { id: string; name: string };

export function PublishDialog({ asset, accounts, open, onOpenChange }: { asset: GeneratedAsset; accounts: Opt[]; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [account, setAccount] = useState(''); const [campaigns, setCampaigns] = useState<Opt[]>([]); const [campaign, setCampaign] = useState('');
  const [adsets, setAdsets] = useState<Opt[]>([]); const [adset, setAdset] = useState(''); const [pageId, setPageId] = useState(''); const [link, setLink] = useState('');
  const [headline, setHeadline] = useState(asset.headline); const [text, setText] = useState(asset.primary_text); const [cta, setCta] = useState(asset.cta_type);
  const [status, setStatus] = useState<'PAUSED' | 'ACTIVE'>('PAUSED'); const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { if (account) api.campaigns(account).then(setCampaigns); else setCampaigns([]); setCampaign(''); }, [account]);
  useEffect(() => { if (account && campaign) api.adsets(account, campaign).then(setAdsets); else setAdsets([]); setAdset(''); }, [account, campaign]);

  const submit = async () => {
    const { publish_job_id } = await api.publish(asset.id, { ad_account_id: account, campaign_id: campaign, adset_id: adset, page_id: pageId, link_url: link, headline, primary_text: text, cta_type: cta, status });
    setMsg(`Publish job ${publish_job_id} queued.`);
  };
  const sel = (label: string, value: string, set: (v: string) => void, opts: Opt[]) => (
    <label className="block text-sm">{label}<select className="mt-1 w-full rounded border p-1" value={value} onChange={(e) => set(e.target.value)} aria-label={label}><option value="">Select…</option>{opts.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select></label>);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="space-y-3">
        <DialogHeader><DialogTitle>Publish to Meta</DialogTitle></DialogHeader>
        {sel('Account', account, setAccount, accounts)}{sel('Campaign', campaign, setCampaign, campaigns)}{sel('Ad set', adset, setAdset, adsets)}
        <label className="block text-sm">Page ID<Input value={pageId} onChange={(e) => setPageId(e.target.value)} /></label>
        <label className="block text-sm">Link URL<Input value={link} onChange={(e) => setLink(e.target.value)} /></label>
        <label className="block text-sm">Headline<Input value={headline} onChange={(e) => setHeadline(e.target.value)} /></label>
        <label className="block text-sm">Primary text<Input value={text} onChange={(e) => setText(e.target.value)} /></label>
        <label className="block text-sm">CTA<Input value={cta} onChange={(e) => setCta(e.target.value)} /></label>
        <label className="block text-sm">Status <select className="ml-1 rounded border p-1" value={status} onChange={(e) => setStatus(e.target.value as 'PAUSED' | 'ACTIVE')}><option>PAUSED</option><option>ACTIVE</option></select></label>
        <Button onClick={submit} disabled={!account || !campaign || !adset || !pageId || !link}>Publish</Button>
        {msg && <p role="status" className="text-sm">{msg}</p>}
      </DialogContent>
    </Dialog>
  );
}
