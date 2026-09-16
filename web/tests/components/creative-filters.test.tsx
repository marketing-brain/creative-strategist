import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const push = vi.fn();
let params = new URLSearchParams('');
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }), useSearchParams: () => params }));
import { CreativeFilters } from '@/components/creative-filters';

const dimensions = [
  { name: 'hook_type', group: 'hook', kind: 'single' as const, applies_to: ['image', 'video'], values: ['question', 'statistic'], description: '' },
  { name: 'hook_summary', group: 'summary', kind: 'text' as const, applies_to: ['image', 'video'], values: null, description: '' },
];

afterEach(() => {
  params = new URLSearchParams('');
  cleanup();
});

describe('CreativeFilters', () => {
  it('pushes query on format change', async () => {
    render(<CreativeFilters accounts={[{ id: 'a1', name: 'Main' }]} dimensions={[]} />);
    await userEvent.click(screen.getByLabelText(/format/i));
    await userEvent.click(await screen.findByRole('option', { name: 'Video' }));
    expect(push).toHaveBeenCalledWith('/creatives?format=video');
  });

  it('offers only dimensions with enumerated values, not free-text ones', async () => {
    render(<CreativeFilters accounts={[]} dimensions={dimensions} />);
    await userEvent.click(screen.getByLabelText(/tag dimension/i));
    expect(await screen.findByRole('option', { name: 'hook_type' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'hook_summary' })).not.toBeInTheDocument();
  });

  it('pushes the dimension alone when no value is picked yet', async () => {
    render(<CreativeFilters accounts={[]} dimensions={dimensions} />);
    await userEvent.click(screen.getByLabelText(/tag dimension/i));
    await userEvent.click(await screen.findByRole('option', { name: 'hook_type' }));
    expect(push).toHaveBeenCalledWith('/creatives?dimension=hook_type');
  });

  it('pushes dimension and value once a value is picked for the selected dimension', async () => {
    params = new URLSearchParams('dimension=hook_type');
    render(<CreativeFilters accounts={[]} dimensions={dimensions} />);
    await userEvent.click(screen.getByLabelText(/tag value/i));
    await userEvent.click(await screen.findByRole('option', { name: 'question' }));
    expect(push).toHaveBeenCalledWith('/creatives?dimension=hook_type&value=question');
  });

  it('clears a stale value when the dimension changes', async () => {
    params = new URLSearchParams('dimension=hook_type&value=question');
    render(<CreativeFilters accounts={[]} dimensions={dimensions} />);
    await userEvent.click(screen.getByLabelText(/tag dimension/i));
    await userEvent.click(await screen.findByRole('option', { name: 'None' }));
    expect(push).toHaveBeenCalledWith('/creatives');
  });
});
