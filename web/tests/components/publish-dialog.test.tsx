import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const publish = vi.fn().mockResolvedValue({ publish_job_id: 'p1' });
vi.mock('@/lib/api', async () => ({ ...(await vi.importActual<typeof import('@/lib/api')>('@/lib/api')), api: {
  campaigns: vi.fn().mockResolvedValue([{ id: 'c1', name: 'Camp' }]),
  adsets: vi.fn().mockResolvedValue([{ id: 'as1', name: 'Set' }]),
  publish: (id: string, b: unknown) => publish(id, b),
} }));
import { PublishDialog } from '@/components/publish-dialog';

const asset = { id: 'a1', generation_id: 'g', kind: 'image' as const, aspect: '1:1', variant: 1, url: 'u', headline: 'H', primary_text: 'P', cta_type: 'SHOP_NOW', review_state: 'approved' as const };

describe('PublishDialog', () => {
  it('walks account -> campaign -> adset and publishes paused', async () => {
    render(<PublishDialog asset={asset} accounts={[{ id: 'acc1', name: 'Main' }]} open onOpenChange={() => {}} />);
    await userEvent.click(screen.getByLabelText(/account/i));
    await userEvent.click(await screen.findByRole('option', { name: 'Main' }));
    await userEvent.click(await screen.findByLabelText(/campaign/i));
    await userEvent.click(await screen.findByRole('option', { name: 'Camp' }));
    await userEvent.click(await screen.findByLabelText(/ad set/i));
    await userEvent.click(await screen.findByRole('option', { name: 'Set' }));
    await userEvent.type(screen.getByLabelText(/page id/i), 'pg');
    await userEvent.type(screen.getByLabelText(/link url/i), 'https://x');
    await userEvent.click(screen.getByRole('button', { name: /publish/i }));
    expect(publish).toHaveBeenCalledWith('a1', expect.objectContaining({ ad_account_id: 'acc1', campaign_id: 'c1', adset_id: 'as1', page_id: 'pg', link_url: 'https://x', status: 'PAUSED' }));
  });
});
