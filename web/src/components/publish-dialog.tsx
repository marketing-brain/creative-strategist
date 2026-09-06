'use client';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ApiError, api } from '@/lib/api';
import type { GeneratedAsset } from '@/lib/types';

const failure = (verb: string, e: unknown) =>
  e instanceof ApiError && e.status === 403
    ? "You don't have permission to do this — ask an admin."
    : `Failed to ${verb}: ${e instanceof Error ? e.message : 'Something went wrong.'}`;

type Opt = { id: string; name: string };

export function PublishDialog({ asset, accounts, open, onOpenChange }: { asset: GeneratedAsset; accounts: Opt[]; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [account, setAccount] = useState('');
  const [campaigns, setCampaigns] = useState<Opt[]>([]);
  const [campaign, setCampaign] = useState('');
  const [adsets, setAdsets] = useState<Opt[]>([]);
  const [adset, setAdset] = useState('');
  const [pageId, setPageId] = useState('');
  const [link, setLink] = useState('');
  const [headline, setHeadline] = useState(asset.headline);
  const [text, setText] = useState(asset.primary_text);
  const [cta, setCta] = useState(asset.cta_type);
  const [status, setStatus] = useState<'PAUSED' | 'ACTIVE'>('PAUSED');
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (account) api.campaigns(account).then(setCampaigns).catch((e) => { setCampaigns([]); setMsg(failure('load campaigns', e)); });
    else setCampaigns([]);
    setCampaign('');
  }, [account]);
  useEffect(() => {
    if (account && campaign) api.adsets(account, campaign).then(setAdsets).catch((e) => { setAdsets([]); setMsg(failure('load ad sets', e)); });
    else setAdsets([]);
    setAdset('');
  }, [account, campaign]);

  // Publishing spends money: guard against a double-submit, and never fail silently.
  const submit = async () => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const { publish_job_id } = await api.publish(asset.id, { ad_account_id: account, campaign_id: campaign, adset_id: adset, page_id: pageId, link_url: link, headline, primary_text: text, cta_type: cta, status });
      setMsg(`Publish job ${publish_job_id} queued.`);
    } catch (e) {
      setMsg(failure('publish', e));
    } finally {
      setBusy(false);
    }
  };

  const picker = (label: string, value: string, set: (v: string) => void, opts: Opt[], disabled = false) => (
    <label className="flex flex-col gap-1.5 text-sm font-medium">
      {label}
      <Select value={value} onValueChange={(v) => set(v ?? '')} disabled={disabled}>
        <SelectTrigger aria-label={label} className="w-full">
          <SelectValue placeholder="Select…">{(v: string) => opts.find((o) => o.id === v)?.name ?? 'Select…'}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">Select…</SelectItem>
          {opts.map((o) => (
            <SelectItem key={o.id} value={o.id}>
              {o.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-md overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Publish to Meta</DialogTitle>
          <DialogDescription>This creates a live ad job on the selected account. Review the details before publishing.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {picker('Account', account, setAccount, accounts)}
          {picker('Campaign', campaign, setCampaign, campaigns, !account)}
          {picker('Ad set', adset, setAdset, adsets, !campaign)}
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Page ID
            <Input value={pageId} onChange={(e) => setPageId(e.target.value)} autoComplete="off" />
          </label>
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Link URL
            <Input type="url" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://…" autoComplete="off" />
          </label>
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Headline
            <Input value={headline} onChange={(e) => setHeadline(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Primary text
            <Input value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            CTA
            <Input value={cta} onChange={(e) => setCta(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Status
            <Select value={status} onValueChange={(v) => setStatus(v as 'PAUSED' | 'ACTIVE')}>
              <SelectTrigger aria-label="Status" className="w-full">
                <SelectValue>{(v: string) => (v === 'ACTIVE' ? 'Active' : 'Paused')}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="PAUSED">Paused</SelectItem>
                <SelectItem value="ACTIVE">Active</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <Button onClick={submit} disabled={busy || !account || !campaign || !adset || !pageId || !link} className="w-full">
            {busy ? 'Publishing…' : 'Publish'}
          </Button>
          {msg && (
            <p role="status" className="text-sm">
              {msg}
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
