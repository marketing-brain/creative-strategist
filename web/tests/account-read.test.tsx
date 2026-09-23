import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AccountReadSection } from '@/components/account-read';
import { buildAccountReadView } from '@/lib/account-read-view';
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

const section = (d: AccountRead) => (
  <AccountReadSection status={d.status} nUnits={d.n_units} view={buildAccountReadView(d)} coverage={d.coverage} />
);

describe('AccountReadSection', () => {
  it('says how much of the account the read covered', () => {
    render(section({ ...done, coverage: { files: 363, tagged_files: 170, spend_share: 0.952 } }));
    expect(screen.getByText(/12 of 363 videos/)).toBeInTheDocument();
    expect(screen.getByText(/95% of account spend/)).toBeInTheDocument();
  });

  it('claims no share when the read named no files', () => {
    render(section({ ...done, coverage: { files: 363, tagged_files: 170, spend_share: null } }));
    expect(screen.getByText(/12 of 363 videos/)).toBeInTheDocument();
    expect(screen.queryByText(/of account spend/)).not.toBeInTheDocument();
  });

  it('leaves the scope line plain when there is no coverage to report', () => {
    render(section(done));
    expect(screen.getByText(/12 videos/)).toBeInTheDocument();
    expect(screen.queryByText(/of account spend/)).not.toBeInTheDocument();
  });

  it('renders the insufficient state with the unit count', () => {
    render(section({ ...done, status: 'insufficient', n_units: 4, read: null }));
    expect(screen.getByText(/needs 10 tagged video files/i)).toBeInTheDocument();
    expect(screen.getByText(/has 4/i)).toBeInTheDocument();
  });

  it('points to Ask when the written read failed its checks', () => {
    render(section({ ...done, status: 'rejected', read: null, error: 'claim P9 not grounded' }));
    expect(screen.getByRole('link', { name: /put a question/i })).toHaveAttribute('href', '/ask');
    expect(screen.queryByText(/not grounded/)).not.toBeInTheDocument();
  });

  it('shows each cited attribute across the whole funnel, with the file linked', () => {
    const d = { ...done, tests: [test(), test({ metric: 'cpa', change: 0.2, cell_value: 48, comp_value: 40, grade: 'directional' })] };
    render(section(d));
    expect(screen.getByText('Warning hooks earn the click')).toBeInTheDocument();
    expect(screen.getByText(/frames the category as risky/)).toBeInTheDocument();
    expect(screen.getByText('hook devices: warning or risk')).toBeInTheDocument();
    expect(screen.getByText('+50%')).toBeInTheDocument();
    // the uncited CPA sibling rides along, so a click win that costs more per purchase is visible
    expect(screen.getByText('+20%')).toHaveClass('text-destructive');
    expect(screen.getAllByText('not tested')).toHaveLength(3);
    expect(screen.getByRole('link', { name: /Worst OTC sleep aids/ })).toHaveAttribute('href', '/creatives/c1');
    // The statistics panel is gone: a buyer reads the findings and the numbers under them,
    // not a census of how many comparisons the run made.
    expect(screen.queryByText(/comparisons run|by luck alone/)).not.toBeInTheDocument();
    // Grades are named for what to do about them, not for their p-value.
    expect(screen.getAllByText(/Likely|Reliable|Early hint|Unproven/).length).toBeGreaterThan(0);
    expect(screen.queryByText('suggestive')).not.toBeInTheDocument();
  });
});
