import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const activate = vi.fn().mockResolvedValue({ job_id: 'j' });
vi.mock('@/lib/api', () => ({ api: { activateAccount: (id: string) => activate(id), syncAccount: vi.fn(), metaOauthStart: vi.fn() } }));

import { AccountsPanel } from '@/components/accounts-panel';

describe('AccountsPanel', () => {
  it('activates a paused account', async () => {
    render(<AccountsPanel accounts={[{ id: 'a1', platform: 'meta', platform_account_id: '1', name: 'Main', currency: 'USD', status: 'paused', sync_cursor: {} }]} />);
    await userEvent.click(screen.getByRole('button', { name: /activate/i }));
    expect(activate).toHaveBeenCalledWith('a1');
    expect(await screen.findByText(/backfill queued/i)).toBeInTheDocument();
  });
});
