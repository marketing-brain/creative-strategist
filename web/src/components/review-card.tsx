'use client';
import { useState } from 'react';
import { PublishDialog } from '@/components/publish-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { api } from '@/lib/api';
import type { GeneratedAsset } from '@/lib/types';

export function ReviewCard({ asset, accounts, isAdmin }: { asset: GeneratedAsset; accounts: { id: string; name: string }[]; isAdmin: boolean }) {
  const [state, setState] = useState(asset.review_state);
  const [open, setOpen] = useState(false);
  const act = async (s: 'approved' | 'rejected') => { const { ok } = await (s === 'approved' ? api.approve(asset.id) : api.reject(asset.id)); if (ok) setState(s); };
  return (
    <div className="space-y-2 rounded border p-3 text-sm">
      {asset.kind === 'video' ? <video src={asset.url} controls className="w-full rounded" /> : <img src={asset.url} alt="" className="w-full rounded" />}
      <div className="flex items-center justify-between"><Badge variant="outline">{asset.aspect} · v{asset.variant}</Badge><Badge>{state}</Badge></div>
      <div className="font-medium">{asset.headline}</div><div className="text-muted-foreground">{asset.primary_text}</div>
      <div className="flex gap-2">
        {state === 'draft' && <><Button size="sm" onClick={() => act('approved')}>Approve</Button><Button size="sm" variant="outline" onClick={() => act('rejected')}>Reject</Button></>}
        {state === 'approved' && (isAdmin
          ? <Button size="sm" onClick={() => setOpen(true)}>Publish</Button>
          : <p className="text-muted-foreground">Ask an admin to publish this.</p>)}
      </div>
      {open && <PublishDialog asset={{ ...asset, review_state: state }} accounts={accounts} open={open} onOpenChange={setOpen} />}
    </div>
  );
}
