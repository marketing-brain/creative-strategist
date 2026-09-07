'use client';
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from 'cn';
import { ApiError, api } from '@/lib/api';
import { blobUrl } from '@/lib/blob-url';
import type { GeneratedAsset } from '@/lib/types';

const stateVariant = { draft: 'secondary', approved: 'success', rejected: 'destructive' } as const;

export function ReviewCard({ asset }: { asset: GeneratedAsset }) {
  const [state, setState] = useState(asset.review_state);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const act = async (s: 'approved' | 'rejected') => {
    setError(null);
    try {
      const { ok } = await (s === 'approved' ? api.approve(asset.id) : api.reject(asset.id));
      if (ok) setState(s);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setError("You don't have permission to do this — ask an admin.");
      } else {
        setError(`Failed: ${e instanceof Error ? e.message : 'Something went wrong.'}`);
      }
    }
  };
  const copyDetails = async () => {
    await navigator.clipboard.writeText(`${asset.headline}\n\n${asset.primary_text}\n\nCTA: ${asset.cta_type}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <Card size="sm">
      {asset.kind === 'video' ? (
        <video src={blobUrl(asset.url)} controls className="aspect-square w-full object-cover" />
      ) : (
        <img src={blobUrl(asset.url)} alt={asset.headline} className="aspect-square w-full object-cover" />
      )}
      <CardContent className="space-y-2 text-sm">
        <div className="flex items-center justify-between">
          <Badge variant="outline">
            {asset.aspect} · v{asset.variant}
          </Badge>
          <Badge variant={stateVariant[state]} className="capitalize">
            {state}
          </Badge>
        </div>
        <div className="line-clamp-2 font-medium">{asset.headline}</div>
        <div className="line-clamp-2 text-muted-foreground">{asset.primary_text}</div>
        <div className="flex gap-2 pt-1">
          {state === 'draft' && (
            <>
              <Button size="sm" onClick={() => act('approved')}>
                Approve
              </Button>
              <Button size="sm" variant="outline" onClick={() => act('rejected')}>
                Reject
              </Button>
            </>
          )}
          {state === 'approved' && (
            <>
              <a href={blobUrl(asset.url)} download className={cn(buttonVariants({ size: 'sm' }))}>
                Download
              </a>
              <Button size="sm" variant="outline" onClick={copyDetails}>
                {copied ? 'Copied' : 'Copy details'}
              </Button>
            </>
          )}
        </div>
        {error && (
          <p role="status" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
