import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const { activate, start, finalize } = vi.hoisted(() => ({
  activate: vi.fn().mockResolvedValue({ job_id: 'j' }),
  start: vi.fn().mockResolvedValue({ connect_card_url: 'https://card', connector_id: 'conn_1' }),
  finalize: vi.fn().mockResolvedValue({ ok: true }),
}));
vi.mock('@/lib/api', () => ({
  api: { activateAccount: (id: string) => activate(id), syncAccount: vi.fn(), fivetranConnectStart: start, fivetranConnectFinalize: finalize },
}));

import { AccountsPanel } from '@/components/accounts-panel';

describe('AccountsPanel', () => {
  it('activates a paused account', async () => {
    render(<AccountsPanel accounts={[{ id: 'a1', platform: 'meta', platform_account_id: '1', name: 'Main', currency: 'USD', status: 'paused', sync_cursor: {}, connection_status: 'ready', connection_error: null }]} />);
    await userEvent.click(screen.getByRole('button', { name: /activate/i }));
    expect(activate).toHaveBeenCalledWith('a1');
    expect(await screen.findByText(/backfill queued/i)).toBeInTheDocument();
  });

  it('opens a popup and finalizes on the fivetran:return message', async () => {
    render(<AccountsPanel accounts={[]} />);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue({ close: vi.fn() } as unknown as Window);
    await userEvent.click(screen.getByRole('button', { name: /connect meta/i }));
    expect(start).toHaveBeenCalled();
    expect(openSpy).toHaveBeenCalledWith('https://card', 'fivetran-connect', expect.any(String));

    window.dispatchEvent(new MessageEvent('message', { origin: window.location.origin, data: { type: 'fivetran:return' } }));
    await vi.waitFor(() => expect(finalize).toHaveBeenCalledWith('conn_1'));
    expect(await screen.findByText(/first sync finishes/i)).toBeInTheDocument();
  });
});
