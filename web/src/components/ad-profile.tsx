import { CopyBrief, SeekButton } from '@/components/creative-detail-client';
import { layerName } from '@/components/diagnosis-panel';
import { label, secs, words } from '@/components/tag-list';
import type { Formatters } from '@/lib/format';
import type { CreativePattern, Diagnosis, DiagnosisPlacement, DiagnosisTier, Tag } from '@/lib/types';

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
const valuesOf = (tags: Tag[], dimension: string) => tags.filter((t) => t.dimension === dimension && t.value && t.value !== 'none');

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
  // ThruPlays ÷ plays, not the header's hold rate (ThruPlays ÷ 3-second views).
  thruplay_rate: 'plays past 15s',
  cpa: 'cost per purchase',
  purchases_per_1k: 'purchases / 1k impressions',
};
// A cost going down is the good direction; every other metric here is a rate going up.
const isGood = (p: CreativePattern) => (p.metric === 'cpa' ? p.change < 0 : p.change > 0);
const signedPct = (x: number) => `${x >= 0 ? '+' : '−'}${Math.round(Math.abs(x) * 100)}%`;

const shown = (p: CreativePattern, v: number | null, fmt: Formatters) =>
  v == null ? '–' : p.metric === 'cpa' ? fmt.money(v) : p.metric === 'purchases_per_1k' ? v.toFixed(2) : fmt.pct(v, 1);
const support = (p: CreativePattern) =>
  `${p.n_cell} of ${p.n_cell + p.n_complement} tagged videos · ${p.grade}${p.confounds.length ? ` · caution: ${p.confounds.map(words).join(', ')}` : ''}`;

// What the caution flags mean, from the rules in account_read/stats._confounds.
const CAUTION: Record<string, string> = {
  budget_concentration: 'one video carries most of this group',
  seasonality: 'these videos ran at a different time of year',
};
const detail = (p: CreativePattern, fmt: Formatters) =>
  [
    `${shown(p, p.cell_value, fmt)} with it vs ${shown(p, p.comp_value, fmt)} without`,
    `${p.n_cell} of ${p.n_cell + p.n_complement} tagged videos have it`,
    p.grade,
    ...p.confounds.map((c) => `caution: ${CAUTION[c] ?? words(c)}`),
  ].join(' · ');

// One finding per line, in columns: effect, metric, what. Colour carries good or bad; the
// figures behind it are on hover. A row with nothing says so: "nothing stands out" is a
// finding too, and every layer answers both questions.
type Item = { key: string; change: number; good: boolean; metric: string; what: string; hover: string };

const patternItem = (p: CreativePattern, fmt: Formatters): Item => ({
  key: `${p.has}-${p.dimension}-${p.value}-${p.metric}`,
  change: p.change,
  good: isGood(p),
  metric: METRIC_LABEL[p.metric],
  what: traitPhrase(p),
  hover: detail(p, fmt),
});

function ItemGroup({ title, items }: { title: string; items: Item[] }) {
  return (
    <div className="grid grid-cols-[4.5rem_3.5rem_11rem_minmax(0,1fr)] items-baseline gap-x-3 gap-y-1 text-sm">
      <span className="text-muted-foreground">{title}</span>
      {items.length === 0 && <span className="col-span-3 text-muted-foreground">nothing stands out</span>}
      {items.map((it, i) => (
        // `contents` has no box to hover, so each cell carries the figures.
        <div key={it.key} className="contents">
          {i > 0 && <span />}
          <span title={it.hover} className={`font-semibold tabular-nums ${it.good ? 'text-success' : 'text-destructive'}`}>{signedPct(it.change)}</span>
          <span title={it.hover} className="text-muted-foreground">{it.metric}</span>
          <span title={it.hover} className="min-w-0">{it.what}</span>
        </div>
      ))}
    </div>
  );
}

// Fatigue and Context have no account patterns (the account read does not test their
// metrics), so their rows come from the layer's own figures, at the diagnosis's own bar.
const BAR = 0.15; // diagnosis.LIFT_THRESHOLD
const PLACEMENT_SHARE_FLOOR = 0.2; // diagnosis.PLACEMENT_SHARE_FLOOR

function ownItems(tier: DiagnosisTier): Item[] {
  if (tier.key === 'fatigue')
    // Daily frequency is left out: reach is counted per day, so it sits near 1.0 for every ad.
    return tier.metrics
      .filter((m) => m.key !== 'frequency' && m.lift != null && Math.abs(m.lift) >= BAR)
      .map((m) => ({
        key: m.key,
        change: m.lift as number,
        good: (m.lift as number) > 0,
        metric: m.label.replace(/ trend$/, '').toLowerCase(),
        what: 'last 7 days against the 14 before',
        hover: m.note,
      }));
  if (tier.key === 'context')
    // Fit, as the layer computes it: how the ad does in a placement against its own average,
    // after taking out how similar videos do there. Only placements carrying real delivery.
    return (tier.placements ?? []).flatMap((pl) => {
      const fit = pl.hook_fit ?? pl.ctr_fit;
      const metric = pl.hook_fit != null ? 'hook rate' : 'link CTR';
      if (fit == null || (pl.share ?? 0) < PLACEMENT_SHARE_FLOOR || Math.abs(fit) < BAR) return [];
      return [{ key: `${pl.platform}/${pl.position}`, change: fit, good: fit > 0, metric, what: `on ${pl.label}, compared with where else it ran`, hover: `${Math.round((pl.share ?? 0) * 100)}% of delivery` }];
    });
  return [];
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
  // How the ad is made, which decides whether it looks native where Meta ran it.
  context: ['aspect_ratio', 'production', 'messenger', 'captions', 'native_format_cues'],
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

// The opening in three short lines: the on-screen text, the first words spoken, and the hook
// (what the first frames show plus the hook devices). The tagger stores the text
// and the speech as one string joined by " / ".
function OpeningLines({ tags, seekable }: { tags: Tag[]; seekable: boolean }) {
  const shows = valuesOf(tags, 'opening_shows');
  const shot = valuesOf(tags, 'opening_shot')[0];
  const verbatim = tags.find((t) => t.dimension === 'hook_verbatim')?.value;
  const summary = tags.find((t) => t.dimension === 'hook_summary')?.value;
  const [onScreen, said] = verbatim ? (verbatim.includes(' / ') ? verbatim.split(' / ', 2) : [verbatim, '']) : ['', ''];
  // One "Hook" line: what the first frames show and the hook devices, in time order, each with
  // its timestamp so clicking it jumps the video to that moment.
  const opening = shows.length ? shows.map((t) => ({ name: `${words(t.value)} on screen`, t: t.t_start_s ?? 0 })) : shot ? [{ name: words(shot.value), t: shot.t_start_s ?? 0 }] : [];
  const hook = [...opening, ...valuesOf(tags, 'hook_devices').map((t) => ({ name: words(t.value), t: t.t_start_s }))].sort((x, y) => (x.t ?? 0) - (y.t ?? 0));
  const lines: [string, React.ReactNode][] = [];
  if (onScreen.trim()) lines.push(['On screen', `“${onScreen.trim()}”`]);
  if (said.trim()) lines.push(['Says', `“${said.trim()}”`]);
  if (!verbatim && summary) lines.push(['Hook', summary]);
  if (hook.length)
    lines.push([
      'Hook',
      <span key="hook" className="flex flex-wrap gap-x-3">
        {hook.map((h, i) => (
          <span key={`${h.name}-${i}`}>
            {h.name} <Stamp t={h.t} seekable={seekable} />
          </span>
        ))}
      </span>,
    ]);
  if (!lines.length) return null;
  return (
    <div className="space-y-2">
      <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
        {lines.map(([k, v], i) => (
          <div key={i} className="contents">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="min-w-0 break-words">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

// Which of this ad's traits a layer shows. The same rules for every layer:
// - a trait most tagged videos share (70% or more) tells this ad apart from nothing;
// - length is left out of the ThruPlay rate, which counts 15 seconds watched, so longer videos
//   pass it by arithmetic rather than by holding anyone;
// - a trait an earlier layer already shows is not repeated (`shown` carries them down).
const NEAR_UNIVERSAL = 0.7;
function layerTraits(tier: DiagnosisTier, patterns: CreativePattern[], shown: Set<string>) {
  const key = (p: CreativePattern) => `${p.dimension}=${p.value}`;
  const rows = patterns.filter(
    (p) =>
      p.has &&
      TIER_OF_METRIC[p.metric] === tier.key &&
      p.n_cell < NEAR_UNIVERSAL * (p.n_cell + p.n_complement) &&
      !(p.metric === 'thruplay_rate' && p.dimension === 'duration_s') &&
      !shown.has(key(p))
  );
  const hurting = rows.filter((p) => !isGood(p)).slice(0, 3);
  const helping = rows.filter(isGood).slice(0, 3);
  [...hurting, ...helping].forEach((p) => shown.add(key(p)));
  return { hurting, helping };
}

// Short labels for the tag rows, in the same label column as every other row in the layers.
const ROW_LABELS: Record<string, string> = {
  offer_elements: 'Offer',
  proof_devices: 'Proof',
  cta_modes: 'CTA',
  aspect_ratio: 'Shape',
  captions: 'Text',
  native_format_cues: 'Looks like',
  production: 'Filmed',
  messenger: 'On camera',
};

// Plain words for the format facts, so the Context layer reads without ad jargon.
const PLAIN: Record<string, Record<string, string>> = {
  aspect_ratio: { '9:16': 'vertical, fills a phone screen (9:16)', '4:5': 'tall, made for feeds (4:5)', '1:1': 'square (1:1)', '16:9': 'wide, like TV (16:9)' },
  captions: { none: 'no text', subtitles: 'subtitles of what is said', text_cards: 'text cards', both: 'subtitles and text cards' },
  production: {
    phone_ugc: 'on a phone',
    polished_studio: 'in a studio, polished',
    motion_graphics: 'as motion graphics',
    mixed_ugc_broll: 'on a phone, mixed with stock footage',
    screen_capture: 'as a screen recording',
  },
  setting: { home: 'at home', outdoor: 'outdoors', studio_or_neutral: 'against a studio or plain backdrop', screen: 'on screen' },
  messenger: {
    creator_on_camera: 'a creator talking to camera',
    creator_off_camera: 'a creator, heard but not seen',
    expert_on_camera: 'an expert talking to camera',
    founder_on_camera: 'the founder talking to camera',
    actor_scripted: 'an actor, scripted',
    multiple_people: 'several people',
    graphic_only: 'no people, graphics only',
  },
  native_format_cues: {
    platform_caption_style: "the app's own caption style",
    green_screen: 'a green-screen video',
    reply_to_comment: 'a reply to a comment',
    duet_or_stitch: 'a duet or stitch',
    social_post_screenshot: 'a screenshot of a social post',
    message_screenshot: 'a screenshot of a message',
  },
};
const plain = (d: string, v: string) => PLAIN[d]?.[v] ?? words(v);
// "Filmed" is one phrase from two tags: how it was shot and where ("on a phone, at home").
function contextText(d: string, v: string, tags: Tag[]) {
  if (d !== 'production') return plain(d, v);
  const where = valuesOf(tags, 'setting')[0];
  return where && PLAIN.setting[where.value] ? `${plain(d, v)}, ${PLAIN.setting[where.value]}` : plain(d, v);
}

// Where Meta spent this ad's delivery, as one bar split by placement. Placements the ad does
// clearly better or worse on (the layer's own fit rule: 20%+ of delivery, 15%+ against its
// other placements) are tinted; the rest are grey. Slivers under 4% are pooled as "other".
function WhereItRan({ placements }: { placements: DiagnosisPlacement[] }) {
  const rows = placements.filter((p) => p.share != null && p.share > 0).sort((a, b) => (b.share ?? 0) - (a.share ?? 0));
  if (!rows.length) return null;
  const big = rows.filter((p) => (p.share ?? 0) >= 0.04);
  const other = rows.filter((p) => (p.share ?? 0) < 0.04).reduce((t, p) => t + (p.share ?? 0), 0);
  const tint = (p: DiagnosisPlacement) => {
    const fit = p.hook_fit ?? p.ctr_fit;
    if (fit == null || (p.share ?? 0) < 0.2 || Math.abs(fit) < 0.15) return 'bg-muted-foreground/25';
    return fit > 0 ? 'bg-success/40' : 'bg-destructive/40';
  };
  const name = (p: DiagnosisPlacement) => p.label.replace(' · ', ' ');
  const segs = [...big.map((p) => ({ key: `${p.platform}/${p.position}`, label: name(p), share: p.share as number, cls: tint(p) })), ...(other > 0 ? [{ key: 'other', label: 'other', share: other, cls: 'bg-muted-foreground/10' }] : [])];
  return (
    <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 text-sm">
      <dt className="text-muted-foreground">Ran on</dt>
      <dd className="min-w-0">
        <div className="flex h-7 w-full overflow-hidden rounded-md">
          {segs.map((sg) => (
            <div key={sg.key} className={`flex min-w-0 items-center justify-center border-r border-background px-1 last:border-r-0 ${sg.cls}`} style={{ width: `${sg.share * 100}%` }} title={`${sg.label}: ${Math.round(sg.share * 100)}% of this ad's delivery`}>
              {sg.share >= 0.2 && <span className="truncate text-xs">{`${sg.label} ${Math.round(sg.share * 100)}%`}</span>}
            </div>
          ))}
        </div>
        {/* Segments too narrow to name inside the bar are named here, in the same order. */}
        {segs.some((sg) => sg.share < 0.2) && (
          <p className="mt-1 text-muted-foreground">
            {`Also ${segs
              .filter((sg) => sg.share < 0.2)
              .map((sg) => `${sg.label} ${Math.round(sg.share * 100)}%`)
              .join(', ')}`}
          </p>
        )}
      </dd>
    </dl>
  );
}

function TierEvidence({
  tier,
  tags,
  traits,
  seekable,
  fmt,
}: {
  tier: DiagnosisTier;
  tags: Tag[];
  traits: { hurting: CreativePattern[]; helping: CreativePattern[] };
  seekable: boolean;
  fmt: Formatters;
}) {
  const attention = tier.key === 'attention';
  const rows = (attention ? [] : TIER_TAGS[tier.key]).map((d) => [d, valuesOf(tags, d)] as const).filter(([, v]) => v.length);
  const drop = tier.key === 'retention' ? dropBeats(tier, tags) : null;
  // Retention lists the ad's beats the way Attention lists its hook: name, then a timestamp
  // that plays the video from there. The opening beat is Attention's Hook row, so it is left out.
  const beats =
    tier.key === 'retention'
      ? valuesOf(tags, 'beats')
          .filter((b) => b.t_start_s !== null && b.value !== 'hook')
          .sort((a, b) => (a.t_start_s ?? 0) - (b.t_start_s ?? 0))
      : [];
  const own = ownItems(tier);
  const hurting = tier.key === 'fatigue' || tier.key === 'context' ? own.filter((i) => !i.good) : traits.hurting.map((p) => patternItem(p, fmt));
  const helping = tier.key === 'fatigue' || tier.key === 'context' ? own.filter((i) => i.good) : traits.helping.map((p) => patternItem(p, fmt));
  // Attention and Retention are label-column rows all the way down, so nothing needs dividing;
  // the other layers still separate their metric blocks from the evidence under them.
  // Every layer is label-column rows now, so nothing needs a divider.
  const rowsOnly = true;
  return (
    // Attention prints no numbers of its own, so nothing sits above to divide it from.
    <div className={rowsOnly ? 'space-y-4' : 'space-y-4 border-t border-dashed border-border pt-4'}>
      {attention && <OpeningLines tags={tags} seekable={seekable} />}
      {tier.key === 'context' && tier.placements && <WhereItRan placements={tier.placements} />}
      {(rows.length > 0 || drop) && (
        <div className="space-y-2">
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
            <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
              {rows.map(([d, v]) => (
                <div key={d} className="contents">
                  <dt className="text-muted-foreground">{ROW_LABELS[d] ?? label(d)}</dt>
                  <dd className="flex min-w-0 flex-wrap gap-x-3">
                    {v.map((t, i) => (
                      <span key={`${t.value}-${i}`}>
                        {tier.key === 'context' ? contextText(d, t.value, tags) : words(t.value)} {tier.key !== 'context' && <Stamp t={t.t_start_s} seekable={seekable} />}
                      </span>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}
      {beats.length > 0 && (
        <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 text-sm">
          <dt className="text-muted-foreground">Sections</dt>
          <dd className="flex min-w-0 flex-wrap gap-x-3">
            {beats.map((b, i) => (
              <span key={`${b.value}-${i}`}>
                {words(b.value)} <Stamp t={b.t_start_s} seekable={seekable} />
              </span>
            ))}
          </dd>
        </dl>
      )}
      <div className="space-y-2">
        <ItemGroup title="Hurting" items={hurting} />
        <ItemGroup title="Helping" items={helping} />
      </div>
    </div>
  );
}

// One block per diagnosis layer, keyed by tier, for the panel to render under its numbers.
// Layers are walked in funnel order so a trait lands in the first layer that shows it.
export function tierEvidence(diagnosis: Diagnosis, tags: Tag[], patterns: CreativePattern[], seekable: boolean, fmt: Formatters) {
  const shown = new Set<string>();
  const byTier = [...diagnosis.tiers].sort((a, b) => a.tier - b.tier).map((t) => [t, layerTraits(t, patterns, shown)] as const);
  return Object.fromEntries(
    byTier.map(([t, traits]) => [t.key, <TierEvidence key={t.key} tier={t} tags={tags} traits={traits} seekable={seekable} fmt={fmt} />])
  ) as Partial<Record<DiagnosisTier['key'], React.ReactNode>>;
}

// Said once, under the panel, for every account pattern on the page.
export const PATTERN_FOOTNOTE =
  'Account patterns compare videos in this account that share a trait with those that do not. They are measured across videos, not inside this ad, so each one is a lead to test and not the cause of this ad’s numbers.';

// ---- what to test next ---------------------------------------------------------------------

type Lead = { p: CreativePattern; text: string; why: string | null };

// A test as an editor would read it in a brief. Taxonomy labels ("first 3 seconds show:
// product") are exact but read like a database. Anything not listed falls back to
// "with / without value (dimension)".
const an = (w: string) => (/^[aeiou]/i.test(w) ? `an ${w}` : `a ${w}`);
const CAPTIONS: Record<string, string> = {
  none: 'no on-screen text',
  subtitles: 'subtitles',
  text_cards: 'text cards',
  both: 'subtitles and text cards',
};
const REVEAL: Record<string, string> = {
  'under 5s': 'the product on screen within 5 seconds',
  '5 to 10s': 'the product first shown at 5–10 seconds',
  '10s to midpoint': 'the product held back until after 10 seconds',
  'second half': 'the product held back to the second half',
  never: 'no product shot',
};
function traitPhrase(p: CreativePattern): string {
  const v = words(p.value);
  if (p.bin_edges) {
    const [lo, hi] = p.bin_edges.map((n) => Math.round(n * 10) / 10);
    const [a, b] = p.bin_edges.map(Math.round);
    if (p.dimension === 'duration_s') return p.value === 'low' ? `a cut shorter than ${a}s` : p.value === 'high' ? `a cut longer than ${b}s` : `a ${a}–${b}s cut`;
    return `${label(p.dimension)} ${p.value === 'low' ? `under ${lo}` : p.value === 'high' ? `over ${hi}` : `${lo} to ${hi}`}`;
  }
  switch (p.dimension) {
    case 'opening_shows':
      return `${p.value === 'face' ? 'a face' : `the ${v}`} in the first 3 seconds`;
    case 'opening_shot':
    case 'opening_scene':
      return `${an(v)} opening`;
    case 'first_product_shown':
      return REVEAL[p.value] ?? `the product first shown ${v}`;
    case 'hook_devices':
      return `${an(v)} hook`;
    case 'ending':
      return `${an(v)} ending`;
    case 'aspect_ratio':
      return `a ${p.value} cut`;
    case 'captions':
      return CAPTIONS[p.value] ?? v;
    case 'messenger':
      return an(v);
    case 'setting':
      return p.value === 'none' ? 'no live footage' : `${an(v)} setting`;
    case 'brand_presence':
      return p.value === 'none' ? 'no logo or brand name' : `${an(v)} logo or brand name`;
    case 'proof_devices':
      return `${an(v)} as proof`;
    case 'offer_elements':
      return `${an(v)} in the offer`;
    case 'concept_tags':
      return `the “${v}” concept`;
    case 'features_mentioned':
    case 'products_mentioned':
      return `mentions “${v}”`;
    case 'topics':
      return `talks about “${v}”`;
    case 'claims':
      return `the claim “${v}”`;
    case 'production':
      return `${v} production`;
    default:
      return `${v} (${label(p.dimension)})`;
  }
}
function testLine(p: CreativePattern): string {
  // Removing the product from the opening is half an instruction; say where it goes instead.
  if (p.has && p.dimension === 'opening_shows' && p.value === 'product') return 'Test a version that holds the product until after 0:03';
  if (p.has && p.dimension === 'first_product_shown' && p.value === 'under 5s') return 'Test a version that holds the product until after 0:05';
  return `Test a version ${p.has ? 'without' : 'with'} ${traitPhrase(p)}`;
}

// Derived facets carry a fixed sentence about how the label was made, not evidence from this ad.
const DERIVED = new Set(['opening_shows', 'first_product_shown', 'opening_scene']);

// Why the tagger says this ad has the trait. Shown only for traits the ad has: there is no
// evidence for an absence, and a label the reader can check is a label they can correct.
function evidenceFor(p: CreativePattern, tags: Tag[]): string | null {
  if (!p.has || DERIVED.has(p.dimension)) return null;
  const t = tags.find((x) => x.dimension === p.dimension && x.value === p.value);
  return t?.evidence?.trim() || null;
}

// Leads for the layers that need work: a trait this ad has that goes with worse results
// there, or a trait it lacks that goes with better ones. Each is phrased as a test to run,
// which is the honest form of an account-wide effect applied to one ad.
function leadsFor(diagnosis: Diagnosis, patterns: CreativePattern[], tags: Tag[], fmt: Formatters): Lead[] {
  const weak = new Set(diagnosis.tiers.filter((t) => t.status === 'weak').map((t) => t.key));
  // Barred traits (demographics, languages the ad lacks) stay in the evidence, never here.
  const relevant = patterns.filter((p) => weak.has(TIER_OF_METRIC[p.metric]) && p.prescribable !== false);
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
    .map((p) => ({
      p,
      why: evidenceFor(p, tags),
      text: `${testLine(p)}. This ad has ${traitPhrase(p)}; videos with it run ${signedPct(p.change)} on ${METRIC_LABEL[p.metric]} (${proof(p)}).`,
    }));
  const lacks = relevant
    .filter((p) => !p.has)
    .filter(firstPerDimension)
    .slice(0, 3 - Math.min(hurts.length, 1))
    .map((p) => ({
      p,
      why: null,
      text: `${testLine(p)}. This ad lacks it; videos with it run ${signedPct(p.change)} on ${METRIC_LABEL[p.metric]} (${proof(p)}).`,
    }));
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
      return m ? `${layerName(t)} is weak: ${m.label.toLowerCase()} ${show(m.value)} vs ${show(m.baseline)} for the account.` : `${layerName(t)} is weak.`;
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
        {leads.map(({ p }, i) => (
          <li key={i} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-2 text-sm">
            <span className="font-semibold text-muted-foreground tabular-nums">{i + 1}.</span>
            <span className="min-w-0">
              <span className="font-medium">{testLine(p)}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
