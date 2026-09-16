import { Badge } from '@/components/ui/badge';
import type { Dimension, Tag } from '@/lib/types';

const secs = (t: number | null) => (t === null ? '' : `${Number.isInteger(t) ? t : t.toFixed(1)}s`);

function DimensionRow({ name, kind, tags }: { name: string; kind: Tag['kind']; tags: Tag[] }) {
  if (kind === 'ordered') {
    const arc = [...tags].sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0)).map((t) => `${t.value} ${secs(t.t_start_s)}`).join(' → ');
    return (
      <li className="w-full text-sm">
        <span className="text-muted-foreground">{name}</span> <span>{arc}</span>
      </li>
    );
  }
  if (kind === 'text') {
    return (
      <li className="w-full text-sm">
        <span className="text-muted-foreground">{name}</span>
        <p className="mt-0.5 whitespace-pre-wrap">{tags[0]?.value}</p>
      </li>
    );
  }
  return (
    <>
      {tags.map((t, i) => (
        <li key={`${t.dimension}-${t.value}-${i}`}>
          <Badge variant="outline" title={t.evidence || t.raw_value || undefined} className="gap-1.5 font-normal">
            <span className="text-muted-foreground">{name}</span>
            <span>{t.value}</span>
            {kind === 'multi' && t.t_start_s !== null && <span className="tabular-nums text-muted-foreground">@{secs(t.t_start_s)}</span>}
            {kind === 'single' && <span className="tabular-nums text-muted-foreground">{Math.round(t.confidence * 100)}%</span>}
          </Badge>
        </li>
      ))}
    </>
  );
}

export function TagList({ tags, dimensions }: { tags: Tag[]; dimensions: Dimension[] }) {
  const dimOf = Object.fromEntries(dimensions.map((d) => [d.name, d]));
  const groups = new Map<string, Map<string, Tag[]>>();
  for (const t of tags) {
    const g = dimOf[t.dimension]?.group ?? 'other';
    const byDim = groups.get(g) ?? new Map<string, Tag[]>();
    byDim.set(t.dimension, [...(byDim.get(t.dimension) ?? []), t]);
    groups.set(g, byDim);
  }
  return (
    <div className="space-y-4">
      {[...groups.entries()].map(([group, byDim]) => (
        <div key={group}>
          <h4 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{group}</h4>
          <ul className="flex flex-wrap gap-1.5">
            {[...byDim.entries()].map(([name, items]) => (
              <DimensionRow key={name} name={name} kind={items[0].kind ?? dimOf[name]?.kind ?? 'single'} tags={items} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
