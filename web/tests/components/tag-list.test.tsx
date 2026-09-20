import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TagList } from '@/components/tag-list';

describe('TagList', () => {
  it('groups tags by dimension group and shows confidence', () => {
    render(<TagList tags={[{ dimension: 'hook_type', kind: 'single', value: 'question', raw_value: null, confidence: 0.91, evidence: 'opens with a question', t_start_s: null, ordinal: null }]}
      dimensions={[{ name: 'hook_type', group: 'hook', kind: 'single', applies_to: ['image'], values: ['question'], description: 'd' }]} />);
    expect(screen.getByText('Hook')).toBeInTheDocument();
    expect(screen.getByText('question')).toBeInTheDocument();
    expect(screen.getByText('91%')).toBeInTheDocument();
    // Evidence is a focusable tooltip tied to the chip, not a title attribute.
    const note = screen.getByRole('tooltip', { hidden: true });
    expect(note).toHaveTextContent('opens with a question');
    expect(screen.getByText('question').closest('[aria-describedby]')).toHaveAttribute('aria-describedby', note.id);
  });
});
