import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { InsightCard } from '@/components/insight-card';

const base = { id: 'i1', kind: 'pattern' as const, dimension: 'hook_type', value: 'question', metric: 'ctr' as const, creative_id: null,
  window_start: '2026-08-01', window_end: '2026-08-31', sample_creatives: 6, sample_spend: 1500, baseline: 0.02, observed: 0.03, lift: 0.5,
  explanation: 'Questions win', suggested_test: 'Try two more', created_at: '2026-09-01T00:00:00Z' };

describe('InsightCard', () => {
  it('renders a pattern insight with generate link', () => {
    render(<InsightCard insight={base} />);
    expect(screen.getByText('hook_type = question')).toBeInTheDocument();
    expect(screen.getByText('+50% CTR')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /generate from this/i })).toHaveAttribute('href', '/generate?insight=i1');
  });
  it('links fatigue to the creative', () => {
    render(<InsightCard insight={{ ...base, kind: 'fatigue', dimension: 'creative', value: 'c9', creative_id: 'c9', creative_headline: 'Old ad', lift: -0.4 }} />);
    expect(screen.getByRole('link', { name: /old ad/i })).toHaveAttribute('href', '/creatives/c9');
    expect(screen.getByText('-40% CTR')).toBeInTheDocument();
  });
});
