import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const save = vi.fn().mockResolvedValue({ id: 'k', version: 1, colors: ['#000'], fonts: [], voice_rules: 'v', prohibited_claims: ['cures'], logo_url: null });
vi.mock('@/lib/api', () => ({ api: { saveBrandKit: (b: unknown) => save(b) }, apiFetch: vi.fn() }));

import { BrandKitForm } from '@/components/brand-kit-form';

describe('BrandKitForm', () => {
  it('parses lists and saves', async () => {
    render(<BrandKitForm initial={null} />);
    await userEvent.type(screen.getByLabelText(/colors/i), '#000, #fff');
    await userEvent.type(screen.getByLabelText(/voice/i), 'v');
    await userEvent.type(screen.getByLabelText(/prohibited/i), 'cures\nguaranteed');
    await userEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(save).toHaveBeenCalledWith({ colors: ['#000', '#fff'], fonts: [], voice_rules: 'v', prohibited_claims: ['cures', 'guaranteed'] });
    expect(await screen.findByText(/saved version 1/i)).toBeInTheDocument();
  });
});
