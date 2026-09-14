import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CreativeInsightCard } from '@/components/creative-insight';

describe('CreativeInsightCard', () => {
  it('renders the paragraph and the window it was written against', () => {
    render(<CreativeInsightCard insight={{ paragraph: 'Retention is the weak layer.', window_start: '2026-08-15', window_end: '2026-09-13', created_at: '2026-09-14T00:00:00Z' }} />);
    expect(screen.getByText('Retention is the weak layer.')).toBeInTheDocument();
    expect(screen.getByText('Based on 2026-08-15 to 2026-09-13')).toBeInTheDocument();
  });
  it('explains the empty state', () => {
    render(<CreativeInsightCard insight={{ paragraph: null }} />);
    expect(screen.getByText(/no insight yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/based on/i)).not.toBeInTheDocument();
  });
});
