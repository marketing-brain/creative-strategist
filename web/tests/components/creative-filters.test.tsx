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
    expect(await screen.findByRole('option', { name: 'hook type' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'hook_summary' })).not.toBeInTheDocument();
  });

  it('pushes the dimension alone when no value is picked yet', async () => {
    render(<CreativeFilters accounts={[]} dimensions={dimensions} />);
    await userEvent.click(screen.getByLabelText(/tag dimension/i));
    await userEvent.click(await screen.findByRole('option', { name: 'hook type' }));
    expect(push).toHaveBeenCalledWith('/creatives?dimension=hook_type');
  });

  it('pushes dimension and value once a value is picked for the selected dimension', async () => {
    params = new URLSearchParams('dimension=hook_type');
    render(<CreativeFilters accounts={[]} dimensions={dimensions} />);
    await userEvent.click(screen.getByLabelText(/tag value/i));
    await userEvent.click(await screen.findByRole('option', { name: 'question' }));
    expect(push).toHaveBeenCalledWith('/creatives?dimension=hook_type&value=question');
  });

  it('pushes the sort metric, and drops it again for the spend default', async () => {
    render(<CreativeFilters accounts={[]} dimensions={[]} />);
    await userEvent.click(screen.getByLabelText(/sort by/i));
    await userEvent.click(await screen.findByRole('option', { name: 'ROAS' }));
    expect(push).toHaveBeenCalledWith('/creatives?sort=roas');

    params = new URLSearchParams('sort=roas&order=asc');
    cleanup();
    render(<CreativeFilters accounts={[]} dimensions={[]} />);
    await userEvent.click(screen.getByLabelText(/sort by/i));
    await userEvent.click(await screen.findByRole('option', { name: 'Spend' }));
    expect(push).toHaveBeenCalledWith('/creatives');
  });

  it('toggles sort direction and keeps the chosen metric', async () => {
    params = new URLSearchParams('sort=link_ctr');
    render(<CreativeFilters accounts={[]} dimensions={[]} />);
    await userEvent.click(screen.getByLabelText(/high to low/i));
    expect(push).toHaveBeenCalledWith('/creatives?sort=link_ctr&order=asc');
  });

  it('reads cost per purchase cheapest first, and only records a flip away from that', async () => {
    params = new URLSearchParams('sort=cpa');
    render(<CreativeFilters accounts={[]} dimensions={[]} />);
    await userEvent.click(screen.getByLabelText(/low to high/i));
    expect(push).toHaveBeenCalledWith('/creatives?sort=cpa&order=desc');
  });

  it('returns to page 1 when the sort changes', async () => {
    params = new URLSearchParams('sort=roas&page=4');
    render(<CreativeFilters accounts={[]} dimensions={[]} />);
    await userEvent.click(screen.getByLabelText(/sort by/i));
    await userEvent.click(await screen.findByRole('option', { name: 'Link CTR' }));
    expect(push).toHaveBeenCalledWith('/creatives?sort=link_ctr');
  });

  it('searches after a pause in typing and returns to page 1', async () => {
    params = new URLSearchParams('format=video&page=3');
    render(<CreativeFilters accounts={[]} dimensions={[]} />);
    await userEvent.type(screen.getByLabelText(/search creatives/i), 'cortisol');
    await vi.waitFor(() => expect(push).toHaveBeenLastCalledWith('/creatives?format=video&q=cortisol'));
  });

  it('clears a stale value when the dimension changes', async () => {
    params = new URLSearchParams('dimension=hook_type&value=question');
    render(<CreativeFilters accounts={[]} dimensions={dimensions} />);
    await userEvent.click(screen.getByLabelText(/tag dimension/i));
    await userEvent.click(await screen.findByRole('option', { name: 'None' }));
    expect(push).toHaveBeenCalledWith('/creatives');
  });
});
