import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DiagnosisPanel } from '@/components/diagnosis-panel';
import type { Diagnosis, DiagnosisTier } from '@/lib/types';

const tier = (over: Partial<DiagnosisTier> & Pick<DiagnosisTier, 'tier' | 'key' | 'name' | 'status'>): DiagnosisTier => ({
  question: 'Q?',
  decision: null,
  reason: null,
  metrics: [],
  curve: null,
  steps: null,
  ...over,
});

const broken: Diagnosis = {
  window_start: '2026-08-14',
  window_end: '2026-09-12',
  format: 'video',
  baseline_creatives: 41,
  broken_tier: 2,
  verdict: { kind: 'broken', headline: 'Broken layer: Retention. Attention drops between 25% and 50%.', detail: 'Attention is working.' },
  tiers: [
    tier({
      tier: 1,
      key: 'attention',
      name: 'Attention',
      status: 'strong',
      reason: 'Nothing to change here.',
      metrics: [
        { key: 'hook_rate', label: 'Hook rate', value: 0.38, baseline: 0.31, lift: 0.2258, note: '3s views ÷ impressions' },
        { key: 'link_ctr', label: 'Link CTR', value: 0.014, baseline: 0.012, lift: 0.1667, note: 'link clicks ÷ impressions' },
      ],
    }),
    tier({
      tier: 2,
      key: 'retention',
      name: 'Retention',
      status: 'weak',
      decision: 'Attention drops between 25% and 50%.',
      metrics: [{ key: 'hold_rate', label: 'Hold rate', value: 0.21, baseline: 0.34, lift: -0.38, note: 'ThruPlay ÷ 3s views' }],
      curve: {
        points: [
          { p: 0, share: 1, baseline_share: 1 },
          { p: 25, share: 0.71, baseline_share: 0.75 },
          { p: 50, share: 0.29, baseline_share: 0.54 },
          { p: 75, share: 0.18, baseline_share: 0.42 },
          { p: 95, share: 0.14, baseline_share: 0.35 },
          { p: 100, share: 0.13, baseline_share: 0.32 },
        ],
        drop_segment: [25, 50],
        avg_watch_s: 7.1,
        duration_s: 24,
      },
    }),
    tier({
      tier: 3,
      key: 'conversion',
      name: 'Conversion',
      status: 'on_par',
      reason: 'Converts like everyone else.',
      metrics: [{ key: 'cpa', label: 'CPA', value: 41, baseline: 44, lift: 0.068, note: 'spend ÷ conversions' }],
      steps: [
        { key: 'landing_page', label: 'Click → Landing page', rate: 0.64, baseline_rate: 0.61, lift: 0.05, numerator: 3700, denominator: 5800, readable: true },
        { key: 'content_view', label: '→ Content view', rate: 0.48, baseline_rate: 0.45, lift: 0.07, numerator: 1780, denominator: 3700, readable: true },
        { key: 'add_to_cart', label: '→ Add to cart', rate: null, baseline_rate: null, lift: null, numerator: 42, denominator: 90, readable: false },
      ],
    }),
    tier({
      tier: 4,
      key: 'fatigue',
      name: 'Fatigue',
      status: 'weak',
      decision: 'Frequency is climbing faster than the hook is holding.',
      metrics: [
        { key: 'frequency', label: 'Daily frequency', value: 2.3, baseline: 1.4, lift: 0.64, note: 'last 7 days vs first 14' },
        { key: 'hook_rate', label: 'Hook rate trend', value: 0.33, baseline: 0.41, lift: -0.2, note: 'same windows' },
        { key: 'engagement_rate', label: 'Engagement rate', value: 0.019, baseline: 0.021, lift: -0.1, note: 'reactions ÷ impressions' },
      ],
    }),
    tier({ tier: 5, key: 'context', name: 'Context', status: 'not_measured', reason: 'Placement breakdown is not connected.' }),
  ],
};

describe('DiagnosisPanel', () => {
  it('renders the verdict, baseline note, and every tier with its status label', () => {
    render(<DiagnosisPanel diagnosis={broken} />);
    expect(screen.getByText(/Broken layer: Retention/)).toBeInTheDocument();
    expect(screen.getByText('vs 41 other video creatives in this account')).toBeInTheDocument();
    expect(screen.getByText('Strong')).toBeInTheDocument();
    expect(screen.getAllByText('Weak')).toHaveLength(1); // fatigue's weak is labelled differently
    expect(screen.getByText('Early decay')).toBeInTheDocument();
    expect(screen.getByText('On par')).toBeInTheDocument();
    expect(screen.getByText('Not measured')).toBeInTheDocument();
  });

  it('formats ratios as percentages with baseline and lift, and frequency as a plain number', () => {
    render(<DiagnosisPanel diagnosis={broken} />);
    expect(screen.getByText('38%')).toBeInTheDocument();
    expect(screen.getByText('vs 31%')).toBeInTheDocument();
    expect(screen.getByText('+23%')).toBeInTheDocument();
    expect(screen.getByText('−38%')).toBeInTheDocument();
    expect(screen.getByText('2.3')).toBeInTheDocument();
    expect(screen.getByText('was 1.4')).toBeInTheDocument();
    expect(screen.getByText('$41')).toBeInTheDocument();
  });

  it('shows the decision only on weak tiers and the reason otherwise', () => {
    render(<DiagnosisPanel diagnosis={broken} />);
    expect(screen.getByText('Attention drops between 25% and 50%.')).toBeInTheDocument();
    expect(screen.getByText('Nothing to change here.')).toBeInTheDocument();
  });

  it('draws the retention curve with point labels and marks unreadable steps', () => {
    render(<DiagnosisPanel diagnosis={broken} />);
    expect(screen.getByText('p50 · 29%')).toBeInTheDocument();
    expect(screen.getByText(/Avg watch time 7.1s of 24s/)).toBeInTheDocument();
    expect(screen.getByText('42 · below floor')).toBeInTheDocument();
  });

  it('renders healthy and too-early verdicts and not-applicable retention', () => {
    const healthy: Diagnosis = {
      ...broken,
      broken_tier: null,
      verdict: { kind: 'healthy', headline: 'No broken layer.', detail: 'Scale it.' },
      tiers: broken.tiers.map((t) => (t.key === 'retention' ? { ...t, status: 'not_applicable', reason: 'Image creative.', curve: null, metrics: [] } : t)),
    };
    const { unmount } = render(<DiagnosisPanel diagnosis={healthy} />);
    expect(screen.getByText('No broken layer.')).toBeInTheDocument();
    expect(screen.getByText('Not applicable')).toBeInTheDocument();
    expect(screen.getByText('Image creative.')).toBeInTheDocument();
    unmount();
    render(<DiagnosisPanel diagnosis={{ ...broken, verdict: { kind: 'too_early', headline: 'Too early to diagnose.', detail: 'Only 640 impressions.' } }} />);
    expect(screen.getByText('Too early to diagnose.')).toBeInTheDocument();
  });
});
