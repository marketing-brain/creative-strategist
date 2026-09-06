import { Badge } from '@/components/ui/badge';
import type { Dimension, Tag } from '@/lib/types';

export function TagList({ tags, dimensions }: { tags: Tag[]; dimensions: Dimension[] }) {
  const groupOf = Object.fromEntries(dimensions.map((d) => [d.name, d.group]));
  const groups = new Map<string, Tag[]>();
  for (const t of tags) {
    const g = groupOf[t.dimension] ?? 'other';
    groups.set(g, [...(groups.get(g) ?? []), t]);
  }
  return (
    <div className="space-y-4">
      {[...groups.entries()].map(([group, items]) => (
        <div key={group}>
          <h4 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{group}</h4>
          <ul className="flex flex-wrap gap-1.5">
            {items.map((t) => (
              <li key={t.dimension}>
                <Badge variant="outline" title={t.evidence} className="gap-1.5 font-normal">
                  <span className="text-muted-foreground">{t.dimension}</span>
                  <span>{t.value}</span>
                  <span className="tabular-nums text-muted-foreground">{Math.round(t.confidence * 100)}%</span>
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
