// Shapes an account read for the Insights page. The API sends only the tests the read cites
// (each tagged with its `pid`) and their funnel siblings, never the run's full list.
import type { AccountRead, AccountReadFinding, AccountReadTest, AccountReadUnit } from './types';

export const FUNNEL_METRICS = ['hook_rate', 'thruplay_rate', 'link_ctr', 'purchases_per_1k', 'cpa'] as const;
export type FunnelMetric = (typeof FUNNEL_METRICS)[number];

export type FunnelCell = {
  metric: FunnelMetric; change: number; cell_value: number; comp_value: number;
  grade: string; p: number; cited: string | null;
};
export type AttributeRow = {
  key: string; dimension: string; value: string; label: string; group: string | null;
  n_cell: number; n_complement: number; confounds: string[];
  cells: (FunnelCell | null)[]; // one per FUNNEL_METRICS entry, null when the run has no such test
  files: AccountReadUnit[];
};
export type FindingView = Omit<AccountReadFinding, 'exemplars' | 'exception'> & {
  attributes: AttributeRow[];
  exemplars: { name: string; unit: AccountReadUnit | null }[];
  exception: { name: string; unit: AccountReadUnit | null } | null;
};
export type AccountReadView = {
  lede: string; overviewRest: string;
  findings: FindingView[];
  entangled: string; no_evidence: string; next_tests: string[]; data_needed: string[];
  basis: { files: number; creatives: number; spend: number; purchases: number; first_day: string | null; last_day: string | null; created_at: string | null };
  grades: { tests: number; strong: number; suggestive: number; directional: number; thin: number; byChance: number };
  citations: Record<string, string>; // "P2" → plain label, for cite tooltips
};

// "duration_s" → "duration": the unit suffix is for code, the bin edges carry the seconds
const words = (s: string) => s.replace(/_s$/, '').replace(/_/g, ' ');

export function attributeLabel(t: Pick<AccountReadTest, 'dimension' | 'value' | 'bin_edges'>): string {
  let v = words(String(t.value));
  if (t.bin_edges) {
    const [lo, hi] = t.bin_edges;
    v += t.value === 'low' ? ` (under ${lo})` : t.value === 'mid' ? ` (${lo} to ${hi})` : ` (${hi} and up)`;
  }
  return `${words(t.dimension)}: ${v}`;
}

const attrKey = (t: AccountReadTest) => `${t.group ?? ''}|${t.dimension}|${t.value}`;
const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, ' ');

/** First sentence, kept whole when it ends in a citation like "(P1)." */
export function splitLede(text: string): [string, string] {
  const m = text.match(/^([\s\S]+?[.!?])(\s+)(?=[A-Z"'“])/);
  if (!m) return [text, ''];
  return [m[1], text.slice(m[0].length)];
}

export function buildAccountReadView(data: AccountRead): AccountReadView | null {
  const read = data.read;
  if (!read) return null;
  const tests = data.tests ?? [];
  const units = data.units ?? [];
  const bySha = new Map(units.map((u) => [u.sha256, u]));
  const byName = new Map(units.map((u) => [norm(u.headline ?? ''), u]));
  const byAttr = new Map<string, AccountReadTest[]>();
  for (const t of [...tests, ...(data.sibling_tests ?? [])]) {
    const k = attrKey(t);
    const list = byAttr.get(k);
    if (list) list.push(t);
    else byAttr.set(k, [t]);
  }
  // "P<n>" numbers the run's full list. A trimmed list carries the id on each test; a full
  // one (tests, older API) is indexed by position.
  const byPid = new Map(tests.filter((t) => t.pid).map((t) => [t.pid as string, t]));
  const testOf = (pid: string) => byPid.get(pid) ?? (byPid.size === 0 && /^P\d+$/.test(pid) ? tests[Number(pid.slice(1)) - 1] : undefined);
  const named = (name: string) => ({ name, unit: byName.get(norm(name)) ?? null });
  const citations: Record<string, string> = {};

  const findings = read.findings.map((f): FindingView => {
    const rows = new Map<string, AttributeRow>();
    for (const pid of f.pattern_ids) {
      const t = testOf(pid);
      if (!t) continue;
      citations[pid] = attributeLabel(t);
      const k = attrKey(t);
      let row = rows.get(k);
      if (!row) {
        const siblings = byAttr.get(k) ?? [];
        row = {
          key: k, dimension: t.dimension, value: String(t.value), label: attributeLabel(t), group: t.group,
          n_cell: t.n_cell, n_complement: t.n_complement,
          confounds: [...new Set(siblings.flatMap((s) => s.confounds))],
          cells: FUNNEL_METRICS.map((m) => {
            const s = siblings.find((x) => x.metric === m);
            return s ? { metric: m, change: s.change, cell_value: s.cell_value, comp_value: s.comp_value, grade: s.grade, p: s.p, cited: null } : null;
          }),
          files: t.cell_shas.map((s) => bySha.get(s)).filter((u): u is AccountReadUnit => Boolean(u))
            .sort((a, b) => (b.spend ?? 0) - (a.spend ?? 0)).slice(0, 6),
        };
        rows.set(k, row);
      }
      const cell = row.cells[FUNNEL_METRICS.indexOf(t.metric as FunnelMetric)];
      if (cell) cell.cited = pid;
    }
    const hasException = f.exception && !/^none( found)?\.?$/i.test(f.exception.trim());
    const { exemplars, exception, ...rest } = f;
    return { ...rest, attributes: [...rows.values()], exemplars: exemplars.map(named), exception: hasException ? named(exception) : null };
  });

  // Overview and notes cite patterns too; label those for tooltips.
  for (const text of [read.overview, read.entangled, read.no_evidence]) {
    for (const pid of text.match(/P\d+/g) ?? []) {
      const t = testOf(pid);
      if (t && !citations[pid]) citations[pid] = attributeLabel(t);
    }
  }

  const s = (data.summary ?? {}) as Record<string, unknown>;
  const g = (s.by_grade ?? {}) as Record<string, number>;
  const nTests = Number(s.tests ?? 0);
  const [lede, overviewRest] = splitLede(read.overview);
  return {
    lede, overviewRest, findings,
    entangled: read.entangled, no_evidence: read.no_evidence, next_tests: read.next_tests, data_needed: read.data_needed,
    basis: {
      files: Number(s.units ?? data.n_units ?? 0), creatives: Number(s.creatives ?? 0), spend: Number(s.spend ?? 0), purchases: Number(s.purchases ?? 0),
      first_day: (s.first_day as string) ?? data.window_start ?? null, last_day: (s.last_day as string) ?? data.window_end ?? null, created_at: data.created_at ?? null,
    },
    // "suggestive" is p < 0.10 before any correction, so a tenth of all comparisons clear it by luck alone.
    grades: { tests: nTests, strong: g.strong ?? 0, suggestive: g.suggestive ?? 0, directional: g.directional ?? 0, thin: g.thin ?? 0, byChance: Math.round(nTests * 0.1) },
    citations,
  };
}
