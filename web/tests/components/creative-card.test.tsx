import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CreativeCard } from '@/components/creative-card';
import type { Creative } from '@/lib/types';

const base: Creative = {
  id: 'c1',
  ad_account_id: 'a1',
  platform_creative_id: 'pc1',
  format: 'image',
  evidence_format: 'image',
  headline: 'Morning routine',
  primary_text: 'P',
  cta_type: 'SHOP_NOW',
  link_url: null,
  primary_asset_url: '/blobs/org1/creatives/c1.jpg',
  first_seen_at: '2026-08-01T00:00:00Z',
  spend_30d: 1204,
  ctr_30d: 0.023,
};

describe('CreativeCard', () => {
  it('shows spend and CTR for a creative with recent spend', () => {
    render(<CreativeCard creative={base} />);
    expect(screen.getByText('$1,204 · 2.3% CTR')).toBeInTheDocument();
  });

  it('shows a no-spend message instead of $0 · —% CTR', () => {
    render(<CreativeCard creative={{ ...base, spend_30d: 0, ctr_30d: null }} />);
    expect(screen.getByText('No spend in last 30 days')).toBeInTheDocument();
    expect(screen.queryByText(/CTR/)).not.toBeInTheDocument();
  });

  it('labels a proven video without a file as a video, not unresolved', () => {
    render(<CreativeCard creative={{ ...base, format: 'unresolved', evidence_format: 'video', primary_asset_url: null }} />);
    expect(screen.getByText('Video · no file')).toBeInTheDocument();
    expect(screen.getByText('File unavailable')).toBeInTheDocument();
    expect(screen.queryByText(/unresolved/i)).not.toBeInTheDocument();
  });

  it('labels a creative with no proven format as unknown', () => {
    render(<CreativeCard creative={{ ...base, format: 'unresolved', evidence_format: 'unresolved', primary_asset_url: null }} />);
    expect(screen.getAllByText('Unknown format').length).toBeGreaterThan(0);
  });
});
