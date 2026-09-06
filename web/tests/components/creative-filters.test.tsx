import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }), useSearchParams: () => new URLSearchParams('') }));
import { CreativeFilters } from '@/components/creative-filters';

describe('CreativeFilters', () => {
  it('pushes query on format change', async () => {
    render(<CreativeFilters accounts={[{ id: 'a1', name: 'Main' }]} />);
    await userEvent.click(screen.getByLabelText(/format/i));
    await userEvent.click(await screen.findByRole('option', { name: 'Video' }));
    expect(push).toHaveBeenCalledWith('/creatives?format=video');
  });
});
