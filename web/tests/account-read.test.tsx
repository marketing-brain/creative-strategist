import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AccountReadSection } from '@/components/account-read';
import type { AccountRead, AccountReadTest } from '@/lib/types';

const test = (over: Partial<AccountReadTest> = {}): AccountReadTest => ({
  dimension: 'hook_devices', value: 'warning_or_risk', group: null, metric: 'link_ctr', kind: 'per_unit',
  n_cell: 6, n_complement: 6, cell_value: 0.045, comp_value: 0.03, change: 0.5, p: 0.004, grade: 'suggestive',
  confounds: [], cell_shas: ['a'], bin_edges: null, cell_purchases: null, ...over,
});

const done: AccountRead = {
  status: 'done', n_units: 12, created_at: '2026-09-17T00:00:00Z', window_start: '2026-06-01', window_end: '2026-06-30',
  summary: { units: 12, creatives: 20, spend: 12000, purchases: 360, tests: 1, interaction_tests: 0, by_grade: { strong: 0, suggestive: 1, directional: 0, thin: 0 } },
  tests: [test()], overlaps: [], stratified: [], retention_by_reveal: {},
  units: [{ sha256: 'a', headline: 'Worst OTC sleep aids', spend: 900, creative_ids: ['c1'], rep_creative_id: 'c1', duration_s: 60, impressions: 10000, purchases: 30 }],
  read: {
    overview: 'Warning-led pharmacist ads earn the click.',
    findings: [{ title: 'Warning hooks earn the click', layer: 'click', paragraph: 'Link CTR 4.5% with vs 3.0% without (P1).', mechanism: 'The warning frames the category as risky.',
      exemplars: ['Worst OTC sleep aids'], exception: 'none found', why_non_obvious: 'Every warning ad is also a pharmacist.', pattern_ids: ['P1'], confidence: 'suggestive' }],
    entangled: 'none', no_evidence: 'fear untested', next_tests: ['Swap the messenger.'], data_needed: ['More files.'],
  },
  error: null,
};

describe('AccountReadSection', () => {
  it('renders the insufficient state with the unit count', () => {
    render(<AccountReadSection data={{ ...done, status: 'insufficient', n_units: 4, read: null }} accountId="x" />);
    expect(screen.getByText(/needs 10 tagged video files/i)).toBeInTheDocument();
    expect(screen.getByText(/has 4/i)).toBeInTheDocument();
  });

  it('renders findings with mechanism and expands evidence on the evidence toggle', () => {
    render(<AccountReadSection data={done} accountId="x" />);
    expect(screen.getByText('Warning hooks earn the click')).toBeInTheDocument();
    expect(screen.getByText(/frames the category as risky/)).toBeInTheDocument();
    expect(screen.getByText(/Every warning ad is also a pharmacist/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^▸ Evidence/ }));
    expect(screen.getByText(/hook devices = warning or risk/)).toBeInTheDocument();
    // the paragraph already carries the numbers; the evidence line adds a second occurrence
    expect(screen.getAllByText(/4\.5% with vs 3\.0% without/).length).toBeGreaterThan(1);
  });
});
