import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const create = vi.fn().mockResolvedValue({ id: 'g1' });
const push = vi.fn();
vi.mock('@/lib/api', async () => ({ ...(await vi.importActual<typeof import('@/lib/api')>('@/lib/api')), api: { createGeneration: (b: unknown) => create(b) } }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
import { ApiError } from '@/lib/api';
import { GenerateForm } from '@/components/generate-form';

describe('GenerateForm', () => {
  it('submits with the preselected insight', async () => {
    render(<GenerateForm insightId="i1" insights={[{ id: 'i1', label: 'hook_type = question (+50% CTR)' }]} />);
    await userEvent.click(screen.getByRole('button', { name: /generate/i }));
    expect(create).toHaveBeenCalledWith({ kind: 'static', variants: 4, source_insight_id: 'i1', source_creative_ids: [], concept: null });
    expect(push).toHaveBeenCalledWith('/generate?created=g1');
  });

  it('shows the standard permission message for a non-admin, without hiding the form', async () => {
    create.mockRejectedValueOnce(new ApiError(403, 'Forbidden'));
    render(<GenerateForm insightId="i1" insights={[{ id: 'i1', label: 'hook_type = question (+50% CTR)' }]} />);
    await userEvent.click(screen.getByRole('button', { name: /generate/i }));
    expect(await screen.findByRole('status')).toHaveTextContent(
      "You don't have permission to do this — ask an admin.",
    );
  });
});
