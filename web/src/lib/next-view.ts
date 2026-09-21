// Shapes a stored plan for the Next page. The API already sends exactly what the page draws,
// so this only groups rows the payload keeps flat: it must not compute a number of its own.
import type { NextAllocationRow, NextExploreRow, NextPlan } from './types';

export const KIND_ORDER = ['fix', 'replace', 'scale'] as const;
export type AllocationKind = (typeof KIND_ORDER)[number];

// The heading says what the reader would do, not what the diagnosis called it.
export const KIND_LABEL: Record<AllocationKind, string> = {
  fix: 'Fix or cut',
  replace: 'Replace',
  scale: 'Give more budget',
};
export const KIND_BLURB: Record<AllocationKind, string> = {
  fix: 'Spending now, broken at a measured step.',
  replace: 'No layer is broken — the same ad is simply getting a worse return than it did at launch. Brief a replacement rather than recutting it.',
  scale: 'On par or better at every step, and under-funded.',
};

export type AllocationGroup = { kind: AllocationKind; label: string; blurb: string; rows: NextAllocationRow[] };
export type ExploreGroup = { dimension: string; label: string; never: NextExploreRow[]; thin: NextExploreRow[] };

export type NextView = {
  groups: AllocationGroup[];
  allocateEmpty: string | null;
  briefs: NextPlan['make'];
  makeEmpty: string | null;
  explore: ExploreGroup[];
  exploreEmpty: string | null;
  nextTests: string[];
  stale: { days: number; through: string } | null;
  basis: { units: number; spend: number; firstDay: string | null; lastDay: string | null; createdAt: string | null; dataThrough: string | null };
};

// "duration_s" → "duration": the unit suffix is for code. Same rule as the Insights page.
export const words = (s: string) => s.replace(/_s$/, '').replace(/_/g, ' ');

// The API sends attribute labels already rendered by Python's `label()`, which turns a
// dimension into words by swapping underscores for spaces — so `duration_s` arrives as
// "duration s = low (under 51.5s)". Only `duration_s` ends that way, and only this page shows
// these strings, so the suffix is dropped here rather than in `label()`, which also writes the
// account read's prompt and the ask box's facts.
export const cleanLabel = (s: string) => s.replace(/ s = /g, ' = ');

export function buildNextView(plan: NextPlan): NextView {
  const rows = plan.allocate ?? [];
  const briefs = (plan.make ?? []).map((b) => ({
    ...b,
    title: cleanLabel(b.title),
    binding: cleanLabel(b.binding),
    hold: cleanLabel(b.hold),
    downstream: cleanLabel(b.downstream),
    evidence: b.evidence.map((e) => ({ ...e, label: cleanLabel(e.label) })),
    refs: b.refs.map((r) => ({ ...r, shows: cleanLabel(r.shows) })),
  }));
  const groups = KIND_ORDER.map((kind) => ({
    kind,
    label: KIND_LABEL[kind],
    blurb: KIND_BLURB[kind],
    rows: rows.filter((r) => r.kind === kind),
  })).filter((g) => g.rows.length > 0);

  const byDim = new Map<string, ExploreGroup>();
  for (const r of plan.explore ?? []) {
    const g = byDim.get(r.dimension) ?? { dimension: r.dimension, label: words(r.dimension), never: [], thin: [] };
    (r.status === 'never tried' ? g.never : g.thin).push(r);
    byDim.set(r.dimension, g);
  }

  // "the last 30 days" is 30 days of calendar, not of data: when the sync is behind, the
  // window slides forward over days that never arrived. Two days' lag is ordinary reporting
  // delay; past that the page says what it is actually looking at.
  const through = plan.data_through ?? null;
  const built = plan.created_at ?? null;
  const lag =
    through && built ? Math.floor((Date.parse(`${built.slice(0, 10)}T00:00:00Z`) - Date.parse(`${through}T00:00:00Z`)) / 86400000) : 0;

  return {
    groups,
    allocateEmpty: plan.allocate_empty_reason ?? null,
    briefs,
    makeEmpty: plan.make_empty_reason ?? null,
    explore: [...byDim.values()],
    exploreEmpty: plan.explore_empty_reason ?? null,
    nextTests: plan.next_tests ?? [],
    stale: through && lag > 2 ? { days: lag, through } : null,
    basis: {
      units: plan.n_units ?? 0,
      spend: plan.account_spend ?? 0,
      firstDay: plan.window_start ?? null,
      lastDay: plan.window_end ?? null,
      createdAt: plan.created_at ?? null,
      dataThrough: through,
    },
  };
}
