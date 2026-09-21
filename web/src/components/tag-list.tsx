import { ChevronRight } from 'lucide-react';
import { SeekButton } from '@/components/creative-detail-client';
import { Badge } from '@/components/ui/badge';
import type { Dimension, Tag } from '@/lib/types';

export const secs = (t: number | null) => (t === null ? '' : `${Number.isInteger(t) ? t : t.toFixed(1)}s`);
// Taxonomy slugs are snake_case for the model and the database; people read words.
export const words = (slug: string) => slug.replaceAll('_', ' ');
// The taxonomy's dimension names are written for the model and the database. These are the
// same things in a media buyer's words; anything not listed falls back to the slug as words.
const DIMENSION_LABELS: Record<string, string> = {
  messenger: 'who speaks',
  messenger_age_band: 'speaker age',
  messenger_gender_presentation: 'speaker gender',
  production: 'production style',
  registers: 'emotional tone',
  proof_devices: 'proof shown',
  offer_elements: 'offer',
  cta_modes: 'call to action',
  hook_devices: 'hook type',
  native_format_cues: 'native look',
  product_reveal: 'product first appears',
  first_product_shown: 'product first appears',
  opening_shot: 'opening shot',
  opening_shows: 'first 3 seconds show',
  duration_s: 'length (s)',
  cuts_per_10s: 'cuts per 10s',
  concept_tags: 'concepts',
  visual_motifs: 'visuals',
  audience_callouts: 'audience called out',
  comparison_targets: 'compared against',
  features_mentioned: 'features mentioned',
  products_mentioned: 'products mentioned',
  overlay_texts: 'on-screen text',
  message_angle: 'angle',
  proof_type: 'proof shown',
  offer_type: 'offer',
};
export const label = (dimension: string) => DIMENSION_LABELS[dimension] ?? words(dimension);

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
const byOrdinal = (tags: Tag[]) => [...tags].sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0));

// Summaries and the verbatim opening are sentences, not tags: they are left out of the tag
// groups (the opening is shown in the Attention layer as On screen / Says).
const LEAD_DIMENSIONS = ['concept_summary', 'message_summary', 'hook_verbatim', 'hook_summary'] as const;

function Stamp({ t, seekable }: { t: number | null; seekable: boolean }) {
  if (t === null) return null;
  return seekable ? <SeekButton seconds={t} label={secs(t)} /> : <span className="tabular-nums text-muted-foreground">{secs(t)}</span>;
}

// The model's evidence for a tag. A title attribute would hide it from keyboard and touch
// users, so the chip itself takes focus and the note shows on hover, focus or tap.
function Chip({ name, tag, kind, seekable }: { name: string; tag: Tag; kind: Tag['kind']; seekable: boolean }) {
  const note = tag.evidence || tag.raw_value || '';
  const id = `ev-${tag.dimension}-${tag.ordinal ?? 0}-${tag.value}`.replace(/[^\w-]/g, '');
  return (
    <li className="group/chip relative">
      <Badge
        variant="outline"
        tabIndex={note ? 0 : undefined}
        aria-describedby={note ? id : undefined}
        className="h-auto max-w-full gap-1.5 py-0.5 font-normal whitespace-normal focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="text-muted-foreground">{label(name)}</span>
        <span>{words(tag.value)}</span>
        {kind === 'multi' && tag.t_start_s !== null && <Stamp t={tag.t_start_s} seekable={seekable} />}
        {kind === 'single' && <span className="tabular-nums text-muted-foreground">{Math.round(tag.confidence * 100)}%</span>}
      </Badge>
      {note && (
        <span
          id={id}
          role="tooltip"
          className="invisible absolute top-full left-0 z-20 mt-1 w-64 rounded-md border border-border bg-popover p-2 text-xs text-popover-foreground shadow-md group-focus-within/chip:visible group-hover/chip:visible"
        >
          {note}
        </span>
      )}
    </li>
  );
}

function DimensionRow({ name, kind, tags, seekable }: { name: string; kind: Tag['kind']; tags: Tag[]; seekable: boolean }) {
  if (kind === 'ordered') {
    return (
      <li className="w-full text-sm">
        <span className="text-muted-foreground">{label(name)}</span>
        <ol className="mt-1 flex flex-wrap gap-x-1.5 gap-y-1">
          {byOrdinal(tags).map((t, i, all) => (
            <li key={`${t.dimension}-${i}`} className="flex items-center gap-1.5">
              <span>{words(t.value)}</span>
              <Stamp t={t.t_start_s} seekable={seekable} />
              {i < all.length - 1 && <ChevronRight className="size-3 text-muted-foreground" aria-hidden="true" />}
            </li>
          ))}
        </ol>
      </li>
    );
  }
  if (kind === 'series') {
    return (
      <li className="w-full text-sm">
        <span className="text-muted-foreground">{label(name)}</span>
        <ol className="mt-1 space-y-0.5">
          {byOrdinal(tags).map((t, i) => (
            <li key={`${t.dimension}-${i}`} className="flex gap-2">
              <span className="w-12 shrink-0 text-right">
                <Stamp t={t.t_start_s} seekable={seekable} />
              </span>
              <span className="min-w-0 break-words">{t.value}</span>
            </li>
          ))}
        </ol>
      </li>
    );
  }
  if (kind === 'text') {
    return (
      <li className="w-full text-sm">
        <span className="text-muted-foreground">{label(name)}</span>
        <p className="mt-0.5 max-w-prose whitespace-pre-wrap">{tags[0]?.value}</p>
      </li>
    );
  }
  return (
    <>
      {tags.map((t, i) => (
        <Chip key={`${t.dimension}-${t.value}-${i}`} name={name} tag={t} kind={kind} seekable={seekable} />
      ))}
    </>
  );
}

// A video carries a hundred-odd tags. Each group is closed by default and says what is
// inside it, so the section reads as a short index rather than a wall of chips.
export function TagList({ tags, dimensions, seekable = false }: { tags: Tag[]; dimensions: Dimension[]; seekable?: boolean }) {
  const dimOf = Object.fromEntries(dimensions.map((d) => [d.name, d]));
  const groups = new Map<string, Map<string, Tag[]>>();
  for (const t of tags) {
    if ((LEAD_DIMENSIONS as readonly string[]).includes(t.dimension)) continue;
    const g = dimOf[t.dimension]?.group ?? 'other';
    const byDim = groups.get(g) ?? new Map<string, Tag[]>();
    byDim.set(t.dimension, [...(byDim.get(t.dimension) ?? []), t]);
    groups.set(g, byDim);
  }
  return (
    <div className="divide-y divide-border rounded-xl border border-border">
      {[...groups.entries()].map(([group, byDim]) => {
        const all = [...byDim.values()].flat();
        const preview = [...byDim.entries()]
          .filter(([, items]) => !['text', 'series'].includes(items[0].kind))
          // A lone value like "both" or "1.5" means nothing without its dimension's name.
          .flatMap(([name, items]) => items.map((t) => (['single', 'numeric'].includes(t.kind) ? `${label(name)} ${words(t.value)}` : words(t.value))))
          .slice(0, 4)
          .join(', ');
        return (
          <details key={group} className="group/tags">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-sm hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
              <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-open/tags:rotate-90 motion-reduce:transition-none" aria-hidden="true" />
              <span className="font-medium">{sentence(words(group))}</span>
              <span className="tabular-nums text-muted-foreground">{all.length}</span>
              <span className="min-w-0 flex-1 truncate text-right text-muted-foreground group-open/tags:hidden">{preview}</span>
            </summary>
            <ul className="flex flex-wrap gap-1.5 px-4 pt-1 pb-4">
              {[...byDim.entries()].map(([name, items]) => (
                <DimensionRow key={name} name={name} kind={items[0].kind ?? dimOf[name]?.kind ?? 'single'} tags={items} seekable={seekable} />
              ))}
            </ul>
          </details>
        );
      })}
    </div>
  );
}
