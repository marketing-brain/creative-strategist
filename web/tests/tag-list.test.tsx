import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TagList } from '@/components/tag-list';
import type { Dimension, Tag } from '@/lib/types';

const dim = (name: string, group: string, kind: Dimension['kind']): Dimension => ({
  name, group, kind, applies_to: ['video'], values: null, description: '',
});
const tag = (partial: Partial<Tag> & Pick<Tag, 'dimension' | 'kind' | 'value'>): Tag => ({
  confidence: 1, evidence: '', raw_value: null, t_start_s: null, ordinal: null, ...partial,
});

describe('TagList', () => {
  it('renders each kind in its own shape', () => {
    render(
      <TagList
        dimensions={[
          dim('messenger', 'messenger', 'single'),
          dim('duration_s', 'form', 'numeric'),
          dim('hook_devices', 'hook', 'multi'),
          dim('beats', 'structure', 'ordered'),
          dim('concept_tags', 'concept', 'open'),
          dim('hook_verbatim', 'text', 'text'),
        ]}
        tags={[
          tag({ dimension: 'messenger', kind: 'single', value: 'expert_on_camera', confidence: 0.9 }),
          tag({ dimension: 'duration_s', kind: 'numeric', value: '93.2' }),
          tag({ dimension: 'hook_devices', kind: 'multi', value: 'warning_or_risk', t_start_s: 0 }),
          tag({ dimension: 'hook_devices', kind: 'multi', value: 'audience_callout', t_start_s: 1.5 }),
          tag({ dimension: 'beats', kind: 'ordered', value: 'hook', ordinal: 0, t_start_s: 0 }),
          tag({ dimension: 'beats', kind: 'ordered', value: 'problem', ordinal: 1, t_start_s: 5 }),
          tag({ dimension: 'concept_tags', kind: 'open', value: 'pharmacist_warning', raw_value: 'Pharmacist Warning' }),
          tag({ dimension: 'hook_verbatim', kind: 'text', value: 'Worst OTC Sleep Aids!!' }),
        ]}
      />,
    );
    expect(screen.getByText('90%')).toBeInTheDocument();
    expect(screen.getByText('93.2')).toBeInTheDocument();
    expect(screen.getByText('warning_or_risk')).toBeInTheDocument();
    expect(screen.getByText('@1.5s')).toBeInTheDocument();
    expect(screen.getByText('hook 0s → problem 5s')).toBeInTheDocument();
    const chip = screen.getByText('pharmacist_warning');
    expect(chip).toBeInTheDocument();
    expect(chip.closest('li')?.textContent).not.toMatch(/%/);
    expect(screen.getByText('Worst OTC Sleep Aids!!')).toBeInTheDocument();
  });
});
