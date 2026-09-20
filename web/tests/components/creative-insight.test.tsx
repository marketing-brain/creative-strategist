import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CreativeInsightCard } from '@/components/creative-insight';

describe('CreativeInsightCard', () => {
  it('renders the paragraph and the window it was written against', () => {
    render(<CreativeInsightCard insight={{ paragraph: 'Retention is the weak layer.', window_start: '2026-08-15', window_end: '2026-09-13', created_at: '2026-09-14T00:00:00Z' }} />);
    expect(screen.getByText('Retention is the weak layer.')).toBeInTheDocument();
    expect(screen.getByText('Written once from 2026-08-15 to 2026-09-13. The diagnosis above is current.')).toBeInTheDocument();
  });
  it('renders blank-line breaks as separate paragraphs', () => {
    render(<CreativeInsightCard insight={{ paragraph: 'Attention is strong.\n\nConversion is weak.', window_start: '2026-08-15', window_end: '2026-09-13', created_at: '2026-09-14T00:00:00Z' }} />);
    expect(screen.getByText('Attention is strong.').tagName).toBe('P');
    expect(screen.getByText('Conversion is weak.').tagName).toBe('P');
  });
  it('explains the empty state', () => {
    render(<CreativeInsightCard insight={{ paragraph: null }} />);
    expect(screen.getByText(/no insight yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/based on/i)).not.toBeInTheDocument();
  });
  it('names the format limit instead of blaming delivery', () => {
    render(<CreativeInsightCard insight={{ paragraph: null, reason: 'format_limit', format: 'video', limit: 4 }} />);
    expect(screen.getByText(/reached its limit of 4 video insights/i)).toBeInTheDocument();
    expect(screen.queryByText(/enough delivery/i)).not.toBeInTheDocument();
  });
  it.each([
    ['rejected', /failed the accuracy check/i],
    ['no_asset', /has not been downloaded/i],
    ['unresolved', /format is not confirmed/i],
    ['lengths_pending', /still being measured for length/i],
    ['unreadable', /enough delivery/i],
    ['paused', /nightly insight run is paused/i],
    ['waiting', /next nightly insight run/i],
  ] as const)('gives the %s reason its own text', (reason, text) => {
    render(<CreativeInsightCard insight={{ paragraph: null, reason }} />);
    expect(screen.getByText(text)).toBeInTheDocument();
  });
});
