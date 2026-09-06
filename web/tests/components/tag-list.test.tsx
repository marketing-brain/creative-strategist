import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TagList } from '@/components/tag-list';

describe('TagList', () => {
  it('groups tags by dimension group and shows confidence', () => {
    render(<TagList tags={[{ dimension: 'hook_type', value: 'question', confidence: 0.91, evidence: 'opens with a question' }]}
      dimensions={[{ name: 'hook_type', group: 'hook', applies_to: ['image'], values: ['question'], description: 'd' }]} />);
    expect(screen.getByText('hook')).toBeInTheDocument();
    expect(screen.getByText('question')).toBeInTheDocument();
    expect(screen.getByText('91%')).toBeInTheDocument();
    expect(screen.getByTitle('opens with a question')).toBeInTheDocument();
  });
});
