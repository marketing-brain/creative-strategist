import type { Dimension, Tag } from '@/lib/types';

export function TagList({ tags, dimensions }: { tags: Tag[]; dimensions: Dimension[] }) {
  const groupOf = Object.fromEntries(dimensions.map((d) => [d.name, d.group]));
  const groups = new Map<string, Tag[]>();
  for (const t of tags) { const g = groupOf[t.dimension] ?? 'other'; groups.set(g, [...(groups.get(g) ?? []), t]); }
  return (
    <div className="space-y-4">
      {[...groups.entries()].map(([group, items]) => (
        <div key={group}>
          <h4 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">{group}</h4>
          <ul className="flex flex-wrap gap-2">
            {items.map((t) => (
              <li key={t.dimension} title={t.evidence} className="rounded border px-2 py-1 text-xs">
                <span className="text-muted-foreground">{t.dimension}: </span><span>{t.value}</span>
                <span className="ml-2 rounded bg-muted px-1">{Math.round(t.confidence * 100)}%</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
