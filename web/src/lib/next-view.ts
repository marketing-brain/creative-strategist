// Shapes a stored plan for the Next page. The API already sends exactly what the page draws,
// so this only maps and groups what the payload keeps flat: it must not compute a number.
import type { NextAllocationRow, NextCard, NextPlan } from './types';

// Every row on this page is the same sentence: here is an ad, here is what to do with it,
// here is what it is worth. They arrived as two payload shapes built a day apart by two
// generators, and the page was rendering them as two different products -- poster comparisons
// above, coloured rails and rank badges below. One shape, one card.
export type ActionKind = 'change' | 'fix' | 'replace' | 'fund';

export type Action = {
  key: string;
  kind: ActionKind;
  /** The imperative. For a creative change the writer produced it; for a budget row it is the
   *  verb applied to the ad, because a diagnosis ("Broken layer: Conversion") is not an
   *  instruction and the page's heading slot is for instructions. */
  title: string;
  /** The ad itself: machine-named, so never the heading. */
  adName: string;
  posterUrl?: string | null;
  creativeId: string | null;
  /** 30-day spend. The same quantity for both sources, which is what makes one list honest. */
  money: number;
  /** One line of why, already written. */
  detail: string;
  /** Citation, when the claim has one. Budget rows are diagnoses and carry none. */
  cite: string | null;
  /** Only creative changes carry a comparison; a budget row has nothing to compare against. */
  card: NextCard | null;
  /** A budget verdict for the same file, folded in rather than shown as a second row. */
  alsoBudget: string | null;
  /** What this change gives up, when another graded finding argues against it. */
  trade: string | null;
};

export type ActionGroup = {
  kind: 'creative' | 'budget';
  title: string;
  blurb: string;
  actions: Action[];
};

const BUDGET_TITLE: Record<string, (row: NextAllocationRow) => string> = {
  fix: (r) => (r.broken_layer ? `Fix the ${r.broken_layer.toLowerCase()} step` : 'Fix or cut this ad'),
  replace: () => 'Replace this ad',
  scale: () => 'Give this ad more budget',
};

const KIND_OF: Record<string, ActionKind> = { fix: 'fix', replace: 'replace', scale: 'fund' };

/** What a budget row may add to a creative card for the same file.
 *
 *  A `fix` verdict ends in its own prescription — "Recut the opening; keep the rest" — which
 *  landed under a card asking for a whole new shorter cut and told the reader to do two
 *  incompatible things. Its diagnosis is still worth knowing, so only the layer survives.
 *  `replace` and `scale` describe the ad's economics and prescribe no creative change, so
 *  they fold in whole. */
function foldedVerdict(row: NextAllocationRow): string {
  if (row.kind !== 'fix') return row.verdict;
  return row.broken_layer
    ? `This ad is also measured weak at the ${row.broken_layer.toLowerCase()} step.`
    : 'This ad is also flagged as a budget decision.';
}

function fromCard(c: NextCard): Action {
  return {
    key: c.key,
    kind: 'change',
    title: c.written?.title ?? c.lines.headline,
    adName: c.title,
    posterUrl: c.poster_url,
    creativeId: c.rep_creative_id,
    money: c.spend_30d,
    detail: c.written?.job ?? c.lines.job,
    cite: `${c.pid} · ${c.grade}`,
    card: c,
    alsoBudget: null,
    trade: c.lines.trade,
  };
}

function fromRow(r: NextAllocationRow): Action {
  return {
    key: r.key,
    kind: KIND_OF[r.kind] ?? 'fix',
    title: (BUDGET_TITLE[r.kind] ?? (() => 'Review this ad'))(r),
    adName: r.headline,
    posterUrl: r.poster_url,
    creativeId: r.rep_creative_id,
    money: r.stake.amount,
    detail: r.verdict,
    cite: null,
    card: null,
    alsoBudget: null,
    trade: null,
  };
}

export type NextView = {
  groups: ActionGroup[];
  money: number;
  count: number;
  empty: { creative: string | null; budget: string | null };
  basis: { units: number; spend: number; firstDay: string | null; lastDay: string | null; dataThrough: string | null };
};

export function buildNextView(plan: NextPlan): NextView {
  const creative = (plan.recut ?? []).map(fromCard);

  // One video, one row. The two blocks come from two generators that group and name files
  // differently, so the same file was appearing as a creative card and a budget row under two
  // different ad names with two different 30-day figures ($45,947 and $50,983 for one video),
  // and the total at the top of the page counted it twice. Where they collide the creative
  // card wins -- it is the one carrying a cited finding -- and the budget verdict is folded
  // into it so the decision is not lost.
  const byFootage = new Map(creative.map((a) => [a.card?.footage_id, a]));
  const order: ActionKind[] = ['fix', 'replace', 'fund'];
  const budget: Action[] = [];
  for (const row of plan.allocate ?? []) {
    const twin = row.footage_id ? byFootage.get(row.footage_id) : undefined;
    if (twin) {
      twin.alsoBudget = twin.alsoBudget ?? foldedVerdict(row);
      continue;
    }
    budget.push(fromRow(row));
  }
  budget.sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || b.money - a.money);

  const groups: ActionGroup[] = [];
  if (creative.length || plan.recut_empty_reason) {
    groups.push({
      kind: 'creative',
      title: 'Change the creative',
      blurb: 'Each one cites a finding from your account read.',
      actions: creative,
    });
  }
  if (budget.length || plan.allocate_empty_reason) {
    groups.push({
      kind: 'budget',
      title: 'Move the budget',
      blurb: 'Calls on ads already live. No production time needed.',
      actions: budget,
    });
  }

  const all = [...creative, ...budget];
  return {
    groups,
    money: all.reduce((n, a) => n + a.money, 0),
    count: all.length,
    empty: { creative: plan.recut_empty_reason ?? null, budget: plan.allocate_empty_reason ?? null },
    basis: {
      units: plan.n_units ?? 0,
      spend: plan.account_spend ?? 0,
      firstDay: plan.window_start ?? null,
      lastDay: plan.window_end ?? null,
      dataThrough: plan.data_through ?? null,
    },
  };
}
