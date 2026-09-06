import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async () => ({ ...(await vi.importActual<typeof import('@/lib/api')>('@/lib/api')), api: {
  messages: vi.fn().mockResolvedValue([]),
  newConversation: vi.fn().mockResolvedValue({ id: 'c1' }),
  send: vi.fn().mockResolvedValue({ reply: 'Questions win.' }),
} }));
import { CopilotChat } from '@/components/copilot-chat';

describe('CopilotChat', () => {
  it('sends a message and shows the reply', async () => {
    render(<CopilotChat conversations={[]} initialId={null} />);
    await userEvent.type(screen.getByPlaceholderText(/ask/i), 'what works?');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(await screen.findByText('what works?')).toBeInTheDocument();
    expect(await screen.findByText('Questions win.')).toBeInTheDocument();
  });
});
