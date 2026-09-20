import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CreativeCard, extraFor } from '@/components/creative-card';
import { formatters } from '@/lib/format';
import type { Creative, GridRates } from '@/lib/types';

const rates = (over: Partial<GridRates> = {}): GridRates => ({
  spend: 1204,
  impressions: 50_000,
  link_clicks: 900,
  purchases: 30,
  hook_rate: null,
  link_ctr: 0.018,
  cpa: 40,
  roas: 1.5,
  ...over,
});

const base: Creative = {
  id: 'c1',
  ad_account_id: 'a1',
  platform_creative_id: 'pc1',
  format: 'image',
  headline: 'Morning routine',
  primary_text: 'P',
  cta_type: 'SHOP_NOW',
  link_url: null,
  primary_asset_url: '/blobs/org1/creatives/c1.jpg',
  first_seen_at: '2026-08-01T00:00:00Z',
  spend_30d: 1204,
  ad_name: 'UGC_Sarah_Hook3',
  totals: rates(),
  baseline: rates({ spend: 90_000, impressions: 4_000_000, link_clicks: 60_000, cpa: 50, roas: 1.0, link_ctr: 0.015 }),
};
const fmt = formatters('USD');

describe('CreativeCard', () => {
  it('names the card by the ad name and shows results beside the account gap', () => {
    render(<CreativeCard creative={base} fmt={fmt} extra="link_ctr" />);
    expect(screen.getByText('UGC_Sarah_Hook3')).toBeInTheDocument();
    expect(screen.getByText('$1,204')).toBeInTheDocument();
    expect(screen.getByText('1.50x')).toBeInTheDocument();
    expect(screen.getByText('+50%')).toHaveClass('text-success');
    // Cost down prints as minus, and the colour says it is good.
    expect(screen.getByText('−20%')).toHaveClass('text-success');
  });

  it('shows no comparison under the delivery floors', () => {
    render(<CreativeCard creative={{ ...base, totals: rates({ spend: 4, impressions: 113, link_clicks: 2, purchases: 0, cpa: null, roas: 0 }) }} fmt={fmt} extra="link_ctr" />);
    expect(screen.getByText('0.00x')).toBeInTheDocument();
    expect(screen.queryByText(/%$/, { selector: 'span' })).not.toBeInTheDocument();
  });

  it('says so when nothing delivered, instead of a row of zeros', () => {
    render(<CreativeCard creative={{ ...base, totals: rates({ spend: 0, impressions: 0, link_clicks: 0, purchases: 0 }) }} fmt={fmt} extra="link_ctr" />);
    expect(screen.getByText('No delivery in the last 30 days')).toBeInTheDocument();
    expect(screen.queryByText('ROAS')).not.toBeInTheDocument();
  });

  it('lets the fourth number follow the sort', () => {
    expect(extraFor(undefined, 'video')).toBe('hook_rate');
    expect(extraFor(undefined, 'image')).toBe('link_ctr');
    expect(extraFor('purchases', 'video')).toBe('purchases');
  });
});
