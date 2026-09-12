import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MetaConnection } from '@/lib/types';

const { activate, start, finalize, disconnect, refresh } = vi.hoisted(() => ({
  activate: vi.fn().mockResolvedValue({ job_id: 'j' }),
  start: vi.fn().mockResolvedValue({ connect_card_url: 'https://card', connector_id: 'conn_1' }),
  finalize: vi.fn().mockResolvedValue({ ok: true }),
  disconnect: vi.fn().mockResolvedValue({ ok: true }),
  refresh: vi.fn(),
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
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh }) }));

import { AccountsPanel } from '@/components/accounts-panel';
import { ApiError } from '@/lib/api';

const none: MetaConnection = { connected: false, status: null, error: null, last_synced_at: null };
const ready: MetaConnection = { connected: true, status: 'ready', error: null, last_synced_at: '2026-09-10T00:00:00Z' };
const pending: MetaConnection = { connected: true, status: 'pending', error: null, last_synced_at: null };
const errored: MetaConnection = { connected: true, status: 'error', error: 'Fivetran setup is broken -- reconnect from Settings.', last_synced_at: null };

const account = { id: 'a1', platform: 'meta', platform_account_id: '1', name: 'Main', currency: 'USD', status: 'paused' as const, sync_cursor: {}, connection_status: 'ready' as const, connection_error: null };

describe('AccountsPanel', () => {
  it('activates a paused account', async () => {
    render(<AccountsPanel accounts={[account]} connection={ready} />);
    await userEvent.click(screen.getByRole('button', { name: /activate/i }));
    expect(activate).toHaveBeenCalledWith('a1');
    expect(await screen.findByText(/sync queued/i)).toBeInTheDocument();
  });

  it('shows an honest message when activation queues no sync (no Meta connection yet)', async () => {
    activate.mockResolvedValueOnce({ job_id: null });
    render(<AccountsPanel accounts={[account]} connection={none} />);
    await userEvent.click(screen.getByRole('button', { name: /activate/i }));
    expect(activate).toHaveBeenCalledWith('a1');
    expect(await screen.findByText(/connect meta first/i)).toBeInTheDocument();
  });

  it('shows a not-connected state with a single Connect Meta action', () => {
    render(<AccountsPanel accounts={[]} connection={none} />);
    expect(screen.getByText(/not connected/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /connect meta/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /reconnect/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /disconnect/i })).not.toBeInTheDocument();
  });

  it('shows a persistent Reconnect/Disconnect pair once a connection exists, before any account is discovered', () => {
    render(<AccountsPanel accounts={[]} connection={pending} />);
    expect(screen.getByText(/setting up/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reconnect/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /disconnect/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /connect meta/i })).not.toBeInTheDocument();
  });

  it('surfaces the connection error message and last-synced time', () => {
    render(<AccountsPanel accounts={[]} connection={errored} />);
    expect(screen.getByText(/needs attention/i)).toBeInTheDocument();
    expect(screen.getByText(errored.error!)).toBeInTheDocument();

    render(<AccountsPanel accounts={[]} connection={ready} />);
    expect(screen.getByText(/last synced/i)).toBeInTheDocument();
  });

  it('opens a popup and finalizes on the fivetran:return message, then refreshes server state', async () => {
    render(<AccountsPanel accounts={[]} connection={none} />);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue({ close: vi.fn() } as unknown as Window);
    await userEvent.click(screen.getByRole('button', { name: /connect meta/i }));
    expect(start).toHaveBeenCalled();
    expect(openSpy).toHaveBeenCalledWith('https://card', 'fivetran-connect', expect.any(String));

    window.dispatchEvent(new MessageEvent('message', { origin: window.location.origin, data: { type: 'fivetran:return' } }));
    await vi.waitFor(() => expect(finalize).toHaveBeenCalledWith('conn_1'));
    expect(await screen.findByText(/first sync finishes/i)).toBeInTheDocument();
    expect(refresh).toHaveBeenCalled();
  });

  it('disconnects an existing connection after confirmation', async () => {
    render(<AccountsPanel accounts={[]} connection={ready} />);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await userEvent.click(screen.getByRole('button', { name: /disconnect/i }));
    expect(disconnect).toHaveBeenCalled();
    expect(await screen.findByText(/click connect meta to set up a new connection/i)).toBeInTheDocument();
    expect(refresh).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('does not disconnect when the user cancels the confirmation', async () => {
    render(<AccountsPanel accounts={[]} connection={ready} />);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await userEvent.click(screen.getByRole('button', { name: /disconnect/i }));
    expect(disconnect).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('reconnecting disconnects the old connection first, then starts a new one', async () => {
    render(<AccountsPanel accounts={[]} connection={errored} />);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue({ close: vi.fn() } as unknown as Window);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await userEvent.click(screen.getByRole('button', { name: /reconnect/i }));
    expect(disconnect).toHaveBeenCalled();
    await vi.waitFor(() => expect(start).toHaveBeenCalled());
    expect(openSpy).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('does not reconnect when the user cancels the confirmation', async () => {
    render(<AccountsPanel accounts={[]} connection={errored} />);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await userEvent.click(screen.getByRole('button', { name: /reconnect/i }));
    expect(disconnect).not.toHaveBeenCalled();
    expect(start).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('recovers from a stale connection prop via a 409 by refreshing instead of tracking its own flag', async () => {
    start.mockRejectedValueOnce(new ApiError(409, 'A Meta connection already exists -- disconnect it first.'));
    render(<AccountsPanel accounts={[]} connection={none} />);
    await userEvent.click(screen.getByRole('button', { name: /connect meta/i }));
    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();
    expect(refresh).toHaveBeenCalled();
  });
});
