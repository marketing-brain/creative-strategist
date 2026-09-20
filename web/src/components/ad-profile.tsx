import { ArrowDown, ArrowUp } from 'lucide-react';
import { CopyBrief, SeekButton } from '@/components/creative-detail-client';
import { ConceptSummary, label, secs, words } from '@/components/tag-list';
import type { Formatters } from '@/lib/format';
import type { CreativePattern, Diagnosis, DiagnosisTier, Tag } from '@/lib/types';

// What the tags say the ad is. Tags are produced once per file, cost nothing to show, and
// every tagged creative has them, which the written insight (one model call each, capped per
// workspace, frozen at its window) does not.
const FACTS: Record<string, string[]> = {
  video: ['messenger', 'production', 'setting', 'angles', 'registers', 'proof_devices', 'offer_elements'],
  image: ['hook_type', 'message_angle', 'offer_type', 'proof_type', 'visual_style', 'tone'],
  carousel: ['hook_type', 'message_angle', 'offer_type', 'proof_type', 'visual_style', 'tone'],
};

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
const valuesOf = (tags: Tag[], dimension: string) => tags.filter((t) => t.dimension === dimension && t.value && t.value !== 'none');

export function AdProfile({ tags, format }: { tags: Tag[]; format: string }) {
  const facts = (FACTS[format] ?? []).map((d) => [d, valuesOf(tags, d)] as const).filter(([, v]) => v.length);
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold">What this ad is</h2>
      <div className="space-y-4 rounded-xl border border-border bg-card px-6 py-4">
        {tags.length === 0 ? (
          <p className="text-sm text-muted-foreground">Not tagged yet. Once it is, this shows what the ad says, who delivers it and how it opens.</p>
        ) : (
          <>
            <ConceptSummary tags={tags} />
            {facts.length > 0 && (
              <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                {facts.map(([d, v]) => (
                  <div key={d} className="flex min-w-0 gap-2">
                    <dt className="w-32 shrink-0 text-muted-foreground">{label(d)}</dt>
                    <dd className="min-w-0 break-words">{v.map((t) => words(t.value)).join(', ')}</dd>
                  </div>
                ))}
              </dl>
            )}
          </>
        )}
      </div>
    </section>
  );
}

// ---- account patterns ----------------------------------------------------------------------

const TIER_OF_METRIC: Record<CreativePattern['metric'], DiagnosisTier['key']> = {
  hook_rate: 'attention',
  link_ctr: 'attention',
  thruplay_rate: 'retention',
  cpa: 'conversion',
  purchases_per_1k: 'conversion',
};
const METRIC_LABEL: Record<CreativePattern['metric'], string> = {
  hook_rate: 'hook rate',
  link_ctr: 'link CTR',
  thruplay_rate: 'ThruPlay rate',
  cpa: 'cost per purchase',
  purchases_per_1k: 'purchases per 1,000 impressions',
};
// A cost going down is the good direction; every other metric here is a rate going up.
const isGood = (p: CreativePattern) => (p.metric === 'cpa' ? p.change < 0 : p.change > 0);
const signedPct = (x: number) => `${x >= 0 ? '+' : '−'}${Math.round(Math.abs(x) * 100)}%`;

function patternValue(p: CreativePattern) {
  if (!p.bin_edges) return words(p.value);
  const [lo, hi] = p.bin_edges.map((n) => Math.round(n * 10) / 10);
  return p.value === 'low' ? `under ${lo}` : p.value === 'high' ? `over ${hi}` : `${lo} to ${hi}`;
}
const trait = (p: CreativePattern) => `${label(p.dimension)}: ${patternValue(p)}`;
const shown = (p: CreativePattern, v: number | null, fmt: Formatters) =>
  v == null ? '–' : p.metric === 'cpa' ? fmt.money(v) : p.metric === 'purchases_per_1k' ? v.toFixed(2) : fmt.pct(v, 1);
const support = (p: CreativePattern) =>
  `${p.n_cell} of ${p.n_cell + p.n_complement} videos · ${p.grade}${p.confounds.length ? ` · caution: ${p.confounds.map(words).join(', ')}` : ''}`;

// One pattern as a scannable row: direction and size first, then the trait, then the proof.
function PatternRow({ p, fmt }: { p: CreativePattern; fmt: Formatters }) {
  const good = isGood(p);
  const Arrow = p.change < 0 ? ArrowDown : ArrowUp;
  return (
    <li className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 text-sm">
      <span className={`flex items-center gap-1 font-semibold tabular-nums ${good ? 'text-success' : 'text-destructive'}`}>
        <Arrow className="size-3.5" aria-hidden="true" />
        {signedPct(p.change)}
      </span>
      <span className="min-w-0">
        <span className="font-medium">{trait(p)}</span> <span className="text-muted-foreground">on {METRIC_LABEL[p.metric]}</span>
        <span className="block text-xs text-muted-foreground tabular-nums">
          {shown(p, p.cell_value, fmt)} vs {shown(p, p.comp_value, fmt)} · {support(p)}
        </span>
      </span>
    </li>
  );
}

function PatternGroup({ title, rows, fmt }: { title: string; rows: CreativePattern[]; fmt: Formatters }) {
  if (!rows.length) return null;
  return (
    <div className="space-y-1.5">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      <ul className="space-y-2">
        {rows.map((p) => (
          <PatternRow key={`${p.has}-${p.dimension}-${p.value}-${p.metric}`} p={p} fmt={fmt} />
        ))}
      </ul>
    </div>
  );
}

// ---- evidence under each diagnosis layer ---------------------------------------------------

// The API serves opening_shows and first_product_shown where a run has the scenes to derive
// them, and opening_shot / product_reveal where it does not, never both. Listing all four
// shows whichever arrived.
const TIER_TAGS: Record<DiagnosisTier['key'], string[]> = {
  attention: ['hook_devices', 'opening_shows', 'opening_shot', 'first_product_shown', 'product_reveal'],
  retention: [],
  conversion: ['offer_elements', 'proof_devices', 'cta_modes'],
  fatigue: [],
  context: ['aspect_ratio', 'captions', 'native_format_cues'],
};

function Stamp({ t, seekable }: { t: number | null; seekable: boolean }) {
  if (t === null) return null;
  return seekable ? <SeekButton seconds={t} label={mmss(t)} /> : <span className="tabular-nums text-muted-foreground">{secs(t)}</span>;
}

// The beats the steepest retention drop falls across. The diagnosis knows where the curve
// loses the most against similar videos, and the beat tags know what was on screen then.
function dropBeats(tier: DiagnosisTier, tags: Tag[]) {
  const seg = tier.curve?.drop_segment;
  const dur = tier.curve?.duration_s;
  const beats = valuesOf(tags, 'beats')
    .filter((b) => b.t_start_s !== null)
    .sort((a, b) => (a.t_start_s ?? 0) - (b.t_start_s ?? 0));
  if (!seg || !dur || !beats.length) return null;
  const [from, to] = [(seg[0] / 100) * dur, (seg[1] / 100) * dur];
  const during = beats.filter((b, i) => (b.t_start_s ?? 0) < to && (beats[i + 1]?.t_start_s ?? dur) > from);
  return during.length ? { from, to, during } : null;
}

function TierEvidence({ tier, tags, patterns, seekable, fmt }: { tier: DiagnosisTier; tags: Tag[]; patterns: CreativePattern[]; seekable: boolean; fmt: Formatters }) {
  const opening = tier.key === 'attention' ? tags.find((t) => t.dimension === 'hook_verbatim' || t.dimension === 'hook_summary') : undefined;
  const rows = TIER_TAGS[tier.key].map((d) => [d, valuesOf(tags, d)] as const).filter(([, v]) => v.length);
  const mine = patterns.filter((p) => p.has && TIER_OF_METRIC[p.metric] === tier.key);
  const drop = tier.key === 'retention' ? dropBeats(tier, tags) : null;
  if (!opening && !rows.length && !mine.length && !drop) return null;
  return (
    <div className="space-y-4 border-t border-dashed border-border pt-4">
      {(opening || rows.length > 0 || drop) && (
        <div className="space-y-2">
          <h3 className="text-xs font-medium text-muted-foreground">In the ad</h3>
          {opening && <p className="max-w-prose text-sm leading-relaxed">Opens with: “{opening.value}”</p>}
          {drop && (
            <p className="text-sm leading-relaxed">
              The steepest drop against similar videos falls between <Stamp t={drop.from} seekable={seekable} /> and <Stamp t={drop.to} seekable={seekable} />. On screen then:{' '}
              {drop.during.map((b, i) => (
                <span key={`${b.value}-${i}`}>
                  {i > 0 && ', '}
                  {words(b.value)} <Stamp t={b.t_start_s} seekable={seekable} />
                </span>
              ))}
              .
            </p>
          )}
          {rows.length > 0 && (
            <dl className="space-y-1 text-sm">
              {rows.map(([d, v]) => (
                <div key={d} className="flex min-w-0 gap-2">
                  <dt className="w-40 shrink-0 text-muted-foreground">{label(d)}</dt>
                  <dd className="flex min-w-0 flex-wrap gap-x-3">
                    {v.map((t, i) => (
                      <span key={`${t.value}-${i}`}>
                        {words(t.value)} <Stamp t={t.t_start_s} seekable={seekable} />
                      </span>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}
      <PatternGroup title="Traits of this ad that go with worse results in this account" rows={mine.filter((p) => !isGood(p)).slice(0, 3)} fmt={fmt} />
      <PatternGroup title="Traits of this ad that go with better results in this account" rows={mine.filter(isGood).slice(0, 3)} fmt={fmt} />
    </div>
  );
}

// One block per diagnosis layer, keyed by tier, for the panel to render under its numbers.
export function tierEvidence(diagnosis: Diagnosis, tags: Tag[], patterns: CreativePattern[], seekable: boolean, fmt: Formatters) {
  return Object.fromEntries(
    diagnosis.tiers.map((t) => [t.key, <TierEvidence key={t.key} tier={t} tags={tags} patterns={patterns} seekable={seekable} fmt={fmt} />])
  ) as Partial<Record<DiagnosisTier['key'], React.ReactNode>>;
}

// Said once, under the panel, for every account pattern on the page.
export const PATTERN_FOOTNOTE =
  'Account patterns compare videos in this account that share a trait with those that do not. They are measured across videos, not inside this ad, so each one is a lead to test and not the cause of this ad’s numbers.';

// ---- what to test next ---------------------------------------------------------------------

type Lead = { p: CreativePattern; text: string; why: string | null };

// Why the tagger says this ad has the trait. Shown only for traits the ad has: there is no
// evidence for an absence, and a label the reader can check is a label they can correct.
function evidenceFor(p: CreativePattern, tags: Tag[]): string | null {
  if (!p.has) return null;
  const t = tags.find((x) => x.dimension === p.dimension && x.value === p.value);
  return t?.evidence?.trim() || null;
}

// Leads for the layers that need work: a trait this ad has that goes with worse results
// there, or a trait it lacks that goes with better ones. Each is phrased as a test to run,
// which is the honest form of an account-wide effect applied to one ad.
function leadsFor(diagnosis: Diagnosis, patterns: CreativePattern[], tags: Tag[], fmt: Formatters): Lead[] {
  const weak = new Set(diagnosis.tiers.filter((t) => t.status === 'weak').map((t) => t.key));
  const relevant = patterns.filter((p) => weak.has(TIER_OF_METRIC[p.metric]));
  const proof = (p: CreativePattern) => `${shown(p, p.cell_value, fmt)} vs ${shown(p, p.comp_value, fmt)}, ${support(p).replaceAll(' · ', ', ')}`;
  // One lead per idea. `idea` groups attributes the API found to cover nearly the same
  // videos, across metrics as well as dimensions: "the first three seconds show the product"
  // and "the product first appears under 5s" are one instruction to a buyer, even when one
  // of them shows up on hook rate and the other on link CTR. The label catches the rest,
  // since two taxonomy dimensions can both read as "product first appears".
  const seen = new Set<string>();
  const firstPerDimension = (p: CreativePattern) => {
    const keys = [`i${p.idea}`, label(p.dimension)];
    if (keys.some((k) => seen.has(k))) return false;
    keys.forEach((k) => seen.add(k));
    return true;
  };
  const hurts = relevant
    .filter((p) => p.has && !isGood(p))
    .filter(firstPerDimension)
    .slice(0, 2)
    .map((p) => ({ p, why: evidenceFor(p, tags), text: `This ad has ${trait(p)}. Videos that do run ${signedPct(p.change)} on ${METRIC_LABEL[p.metric]} (${proof(p)}). Test a version without it.` }));
  const lacks = relevant
    .filter((p) => !p.has)
    .filter(firstPerDimension)
    .slice(0, 3 - Math.min(hurts.length, 1))
    .map((p) => ({ p, why: null, text: `This ad does not have ${trait(p)}. Videos that do run ${signedPct(p.change)} on ${METRIC_LABEL[p.metric]} (${proof(p)}). Test a version with it.` }));
  return [...hurts, ...lacks];
}

export function NextTests({
  title,
  diagnosis,
  tags,
  patterns,
  fmt,
}: {
  title: string;
  diagnosis: Diagnosis;
  tags: Tag[];
  patterns: CreativePattern[];
  fmt: Formatters;
}) {
  const weak = diagnosis.tiers.filter((t) => t.status === 'weak');
  const leads = leadsFor(diagnosis, patterns, tags, fmt);
  if (!weak.length || !leads.length) return null;
  const problem = weak
    .map((t) => {
      const m = t.metrics.find((x) => x.value != null && x.baseline != null);
      const show = (v: number | null) => (v == null ? '–' : m?.key === 'cpa' ? fmt.money(v) : m?.key === 'frequency' ? v.toFixed(1) : fmt.pct(v, 1));
      return m ? `${t.name} is weak: ${m.label.toLowerCase()} ${show(m.value)} vs ${show(m.baseline)} for the account.` : `${t.name} is weak.`;
    })
    .join(' ');
  const opening = tags.find((t) => t.dimension === 'hook_verbatim' || t.dimension === 'hook_summary')?.value;
  const now = ['hook_devices', 'opening_shows', 'opening_shot', 'first_product_shown', 'product_reveal']
    .map((d) => [d, valuesOf(tags, d)] as const)
    .filter(([, v]) => v.length)
    .map(([d, v]) => `${label(d)}: ${v.map((t) => words(t.value)).join(', ')}`);
  const brief = [
    `Creative: ${title}`,
    `Problem: ${problem}`,
    opening ? `Opens with: "${opening}"` : null,
    now.length ? `Now: ${now.join('; ')}` : null,
    '',
    'Tests to run (patterns across this account, not proof about this ad):',
    ...leads.map((l, i) => `${i + 1}. ${l.text}${l.why ? ` (tagged from: "${l.why}")` : ''}`),
  ]
    .filter((l) => l !== null)
    .join('\n');
  return (
    <div className="space-y-3 border-b border-border px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">What to test next</h3>
        <CopyBrief text={brief} />
      </div>
      <ol className="space-y-2.5">
        {leads.map(({ p, why }, i) => (
          <li key={i} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-2 text-sm">
            <span className="font-semibold text-muted-foreground tabular-nums">{i + 1}.</span>
            <span className="min-w-0">
              <span className="font-medium">{p.has ? 'Try it without' : 'Try it with'} {trait(p)}</span>
              <span className="block text-xs text-muted-foreground tabular-nums">
                This ad {p.has ? 'has it' : 'does not have it'}. Videos that do run{' '}
                <span className={`font-semibold ${isGood(p) ? 'text-success' : 'text-destructive'}`}>{signedPct(p.change)}</span> on {METRIC_LABEL[p.metric]}: {shown(p, p.cell_value, fmt)} vs{' '}
                {shown(p, p.comp_value, fmt)} · {support(p)}
              </span>
              {why && <span className="mt-0.5 block text-xs text-muted-foreground/80 italic">Tagged from: “{why}”</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
