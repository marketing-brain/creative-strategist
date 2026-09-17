import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const accountReadAsk = vi.fn();
vi.mock('@/lib/api', () => ({ api: { accountReadAsk: (...a: unknown[]) => accountReadAsk(...a) } }));
import { AccountReadAsk } from '@/components/account-read-ask';

// jsdom has no layout, so scrollIntoView is not implemented on the element.
Element.prototype.scrollIntoView = vi.fn();

const answer = (text: string) => ({ verdict: 'directional' as const, answer: text, pattern_ids: [], tests: [] });

const ask = async (text: string) => {
  await userEvent.type(screen.getByLabelText(/ask this account a question/i), text);
  await userEvent.click(screen.getByLabelText('Ask'));
};

afterEach(() => {
  accountReadAsk.mockReset();
  cleanup();
});

describe('AccountReadAsk', () => {
  it('keeps earlier exchanges on screen when another question is asked', async () => {
    accountReadAsk.mockResolvedValueOnce(answer('Fear registers convert worse.'));
    accountReadAsk.mockResolvedValueOnce(answer('Over-50s respond to the pharmacist.'));
    render(<AccountReadAsk accountId="a1" tests={[]} units={[]} />);

    await ask('do fear registers convert?');
    expect(await screen.findByText('Fear registers convert worse.')).toBeInTheDocument();

    await ask('which messenger for over-50s?');
    expect(await screen.findByText('Over-50s respond to the pharmacist.')).toBeInTheDocument();

    // Both questions and both answers remain.
    expect(screen.getByText('do fear registers convert?')).toBeInTheDocument();
    expect(screen.getByText('Fear registers convert worse.')).toBeInTheDocument();
    expect(screen.getByText('which messenger for over-50s?')).toBeInTheDocument();
  });

  it('clears the input after asking', async () => {
    accountReadAsk.mockResolvedValue(answer('An answer.'));
    render(<AccountReadAsk accountId="a1" tests={[]} units={[]} />);

    const input = screen.getByLabelText(/ask this account a question/i);
    await ask('a question');
    await waitFor(() => expect(input).toHaveValue(''));
  });

  it('shows the question immediately, before the answer lands', async () => {
    let release: (v: unknown) => void = () => {};
    accountReadAsk.mockReturnValueOnce(new Promise((r) => (release = r)));
    render(<AccountReadAsk accountId="a1" tests={[]} units={[]} />);

    await ask('a slow question');
    expect(screen.getByText('a slow question')).toBeInTheDocument();
    expect(screen.getByText(/reading the account/i)).toBeInTheDocument();

    release(answer('Finally.'));
    expect(await screen.findByText('Finally.')).toBeInTheDocument();
  });

  it('attaches a failure to its own exchange, leaving earlier answers intact', async () => {
    accountReadAsk.mockResolvedValueOnce(answer('A good answer.'));
    accountReadAsk.mockRejectedValueOnce(new Error('Gateway budget exceeded'));
    render(<AccountReadAsk accountId="a1" tests={[]} units={[]} />);

    await ask('first question');
    expect(await screen.findByText('A good answer.')).toBeInTheDocument();

    await ask('second question');
    expect(await screen.findByText('Gateway budget exceeded')).toBeInTheDocument();
    expect(screen.getByText('A good answer.')).toBeInTheDocument();
  });

  it('renders an unmappable reply as plain guidance, not an answer block', async () => {
    accountReadAsk.mockResolvedValueOnce({ unmappable: 'The tags do not capture sentiment yet.' });
    render(<AccountReadAsk accountId="a1" tests={[]} units={[]} />);

    await ask('what ROAS will I get?');
    expect(await screen.findByText('The tags do not capture sentiment yet.')).toBeInTheDocument();
    expect(screen.queryByText('directional')).not.toBeInTheDocument();
  });
});
