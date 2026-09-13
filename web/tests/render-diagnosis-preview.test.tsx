// Not a real test: renders the DiagnosisPanel with realistic data into a static HTML file
// (using the production stylesheet) so the layout can be eyeballed at the real column width.
// Run with: RENDER_PREVIEW=1 npm test -- tests/render-diagnosis-preview.test.tsx
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it } from 'vitest';
import { DiagnosisPanel } from '@/components/diagnosis-panel';
import type { Diagnosis } from '@/lib/types';

const out = process.env.RENDER_PREVIEW_OUT ?? '/tmp/diagnosis-preview.html';

const real: Diagnosis = {
  window_start: '2026-08-15',
  window_end: '2026-09-13',
  format: 'video',
  baseline_creatives: 555,
  broken_tier: 2,
  verdict: {
    kind: 'broken',
    headline: 'Broken layer: Retention. Attention drops between 25% and 50% (6s–12s). Fix pacing or the cut there, or move the CTA earlier.',
    detail: 'Attention is working. Fix this layer before touching anything else.',
  },
  tiers: [
    {
      tier: 1, key: 'attention', name: 'Attention', question: 'Is the opening 1–3s stopping the scroll?', status: 'strong',
      decision: null, reason: 'Nothing to change here. Keep this opening if you recut.',
      metrics: [
        { key: 'hook_rate', label: 'Hook rate', value: 0.58, baseline: 0.396, lift: 0.4646, note: '3s views ÷ impressions · 412,000 impressions' },
        { key: 'link_ctr', label: 'Link CTR', value: 0.033, baseline: 0.036, lift: -0.083, note: 'link clicks ÷ impressions' },
      ],
      curve: null, steps: null,
    },
    {
      tier: 2, key: 'retention', name: 'Retention', question: 'Where in the video does attention drop?', status: 'weak',
      decision: 'Attention drops between 25% and 50% (6s–12s). Fix pacing or the cut there, or move the CTA earlier.', reason: null,
      metrics: [{ key: 'hold_rate', label: 'Hold rate', value: 0.21, baseline: 0.34, lift: -0.38, note: 'ThruPlay ÷ 3s views · 156,000 plays' }],
      curve: {
        points: [
          { p: 0, share: 1, baseline_share: 1 }, { p: 25, share: 0.71, baseline_share: 0.75 }, { p: 50, share: 0.29, baseline_share: 0.54 },
          { p: 75, share: 0.18, baseline_share: 0.42 }, { p: 95, share: 0.14, baseline_share: 0.35 }, { p: 100, share: 0.13, baseline_share: 0.32 },
        ],
        drop_segment: [25, 50], avg_watch_s: 7.3, duration_s: 24,
      },
      steps: null,
    },
    {
      tier: 3, key: 'conversion', name: 'Conversion', question: 'Which funnel step under-delivers?', status: 'on_par',
      decision: null,
      reason: "The people who click convert like everyone else's. The landing page is not the problem. Content view and Add to cart are not tracked consistently for this account, so that step is left out.",
      metrics: [{ key: 'cpa', label: 'CPA', value: 49.9, baseline: 55.1, lift: 0.094, note: 'spend ÷ conversions' }],
      curve: null,
      steps: [
        { key: 'landing_page', label: 'Click → Landing page', rate: 0.905, baseline_rate: 0.912, lift: -0.008, numerator: 5400, denominator: 5967, readable: true },
        { key: 'checkout', label: '→ Checkout', rate: 0.102, baseline_rate: 0.112, lift: -0.089, numerator: 551, denominator: 5400, readable: true },
        { key: 'purchase', label: '→ Purchase', rate: 0.378, baseline_rate: 0.352, lift: 0.074, numerator: 208, denominator: 551, readable: true },
      ],
    },
    {
      tier: 4, key: 'fatigue', name: 'Fatigue', question: 'Is this creative wearing out?', status: 'on_par',
      decision: null, reason: 'No decay signal yet. Frequency and response are moving together.',
      metrics: [
        { key: 'frequency', label: 'Daily frequency', value: 1.03, baseline: 1.054, lift: -0.023, note: 'last 7 days vs first 14' },
        { key: 'hook_rate', label: 'Hook rate trend', value: 0.589, baseline: 0.614, lift: -0.04, note: 'same windows' },
        { key: 'engagement_rate', label: 'Engagement rate', value: 0.005, baseline: 0.007, lift: -0.27, note: 'reactions, comments, saves ÷ impressions' },
      ],
      curve: null, steps: null,
    },
    {
      tier: 5, key: 'context', name: 'Context', question: 'Does it need a native recut per placement?', status: 'not_measured',
      decision: null, reason: 'Placement breakdown (Reels vs Feed vs Stories) is not connected for this account yet. When it is, this row shows hook rate and CTR per placement.',
      metrics: [], curve: null, steps: null,
    },
  ],
};

describe.skipIf(!process.env.RENDER_PREVIEW)('render preview', () => {
  it('writes the preview html', () => {
    const cssDir = join(process.cwd(), '.next/static/css');
    const css = readdirSync(cssDir).filter((f) => f.endsWith('.css')).map((f) => readFileSync(join(cssDir, f), 'utf8')).join('\n');
    const panel = renderToStaticMarkup(<DiagnosisPanel diagnosis={real} />);
    const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>preview</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&display=swap">
<style>${css}</style>
<style>:root{--font-sans:Geist,system-ui,sans-serif}</style></head>
<body class="min-h-dvh bg-background font-sans text-foreground antialiased">
<main style="padding:32px"><div style="width:757px">${panel}</div></main></body></html>`;
    writeFileSync(out, html);
  });
});
