import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const { activate, start, finalize, disconnect } = vi.hoisted(() => ({
  activate: vi.fn().mockResolvedValue({ job_id: 'j' }),
  start: vi.fn().mockResolvedValue({ connect_card_url: 'https://card', connector_id: 'conn_1' }),
  finalize: vi.fn().mockResolvedValue({ ok: true }),
  disconnect: vi.fn().mockResolvedValue({ ok: true }),
}));
vi.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  api: {
    activateAccount: (id: string) => activate(id),
    syncAccount: vi.fn(),
    fivetranConnectStart: start,
    fivetranConnectFinalize: finalize,
    fivetranDisconnect: disconnect,
  },
}));

import { AccountsPanel } from '@/components/accounts-panel';
import { ApiError } from '@/lib/api';

describe('AccountsPanel', () => {
  it('activates a paused account', async () => {
    render(<AccountsPanel accounts={[{ id: 'a1', platform: 'meta', platform_account_id: '1', name: 'Main', currency: 'USD', status: 'paused', sync_cursor: {}, connection_status: 'ready', connection_error: null }]} />);
    await userEvent.click(screen.getByRole('button', { name: /activate/i }));
    expect(activate).toHaveBeenCalledWith('a1');
    expect(await screen.findByText(/sync queued/i)).toBeInTheDocument();
  });

  it('shows an honest message when activation queues no sync (no Meta connection yet)', async () => {
    activate.mockResolvedValueOnce({ job_id: null });
    render(<AccountsPanel accounts={[{ id: 'a1', platform: 'meta', platform_account_id: '1', name: 'Main', currency: 'USD', status: 'paused', sync_cursor: {}, connection_status: 'ready', connection_error: null }]} />);
    await userEvent.click(screen.getByRole('button', { name: /activate/i }));
    expect(activate).toHaveBeenCalledWith('a1');
    expect(await screen.findByText(/connect meta first/i)).toBeInTheDocument();
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

  it('requires explicit disconnect confirmation before deleting an existing connection', async () => {
    start.mockRejectedValueOnce(new ApiError(409, 'A Meta connection already exists -- disconnect it first.'));
    render(<AccountsPanel accounts={[]} />);
    await userEvent.click(screen.getByRole('button', { name: /connect meta/i }));
    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();

    const disconnectButton = await screen.findByRole('button', { name: /disconnect existing connection/i });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await userEvent.click(disconnectButton);
    expect(disconnect).toHaveBeenCalled();
    expect(await screen.findByText(/click connect meta to set up a new connection/i)).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it('does not disconnect when the user cancels the confirmation', async () => {
    start.mockRejectedValueOnce(new ApiError(409, 'A Meta connection already exists -- disconnect it first.'));
    render(<AccountsPanel accounts={[]} />);
    await userEvent.click(screen.getByRole('button', { name: /connect meta/i }));
    const disconnectButton = await screen.findByRole('button', { name: /disconnect existing connection/i });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await userEvent.click(disconnectButton);
    expect(disconnect).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
