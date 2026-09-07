import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ReviewCard } from '@/components/review-card';

const asset = {
  id: 'a1',
  generation_id: 'g',
  kind: 'image' as const,
  aspect: '1:1',
  variant: 1,
  url: '/blobs/org1/creatives/a1.jpg',
  headline: 'H',
  primary_text: 'P',
  cta_type: 'SHOP_NOW',
  review_state: 'approved' as const,
};

describe('ReviewCard', () => {
  it('offers a download link and copies ad copy to the clipboard for an approved asset', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    render(<ReviewCard asset={asset} />);

    const link = screen.getByRole('link', { name: /download/i });
    expect(link).toHaveAttribute('href', '/blobs/org1/creatives/a1.jpg');
    expect(link).toHaveAttribute('download', 'a1-1x1.jpg');

    await userEvent.click(screen.getByRole('button', { name: /copy details/i }));
    expect(writeText).toHaveBeenCalledWith('H\n\nP\n\nCTA: SHOP_NOW');
  });

  it('shows approve/reject for a draft asset, not download', () => {
    render(<ReviewCard asset={{ ...asset, review_state: 'draft' }} />);
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /download/i })).not.toBeInTheDocument();
  });

  it('shows an error message when the clipboard write fails', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    render(<ReviewCard asset={asset} />);

    await userEvent.click(screen.getByRole('button', { name: /copy details/i }));
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Copy failed — select the text above instead.',
    );
  });
});
