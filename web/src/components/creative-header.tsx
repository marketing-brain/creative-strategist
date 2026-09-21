import { BackToGrid } from '@/components/creative-detail-client';
import { Badge } from '@/components/ui/badge';
import type { Creative, Footage } from '@/lib/types';

export function creativeTitle(c: Pick<Creative, 'headline' | 'platform_creative_id'>) {
  return c.headline?.trim() || `Creative ${c.platform_creative_id}`;
}

// What this page is: the way back, the title, the format and whether it is still delivering.
// What its numbers cover lives in ScopePanel, right under this.
export function CreativeHeader({ creative, footage }: { creative: Creative; footage: Footage }) {
  const { ads } = footage;
  const active = ads.filter((a) => a.status === 'ACTIVE').length;
  return (
    <header className="space-y-3">
      <BackToGrid />
      <h1 className="text-2xl font-semibold tracking-tight text-balance break-words">{creativeTitle(creative)}</h1>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className="capitalize">
          {creative.format}
        </Badge>
        {ads.length > 0 && (
          <Badge variant={active ? 'success' : 'outline'}>
            {active} of {ads.length} {ads.length === 1 ? 'ad' : 'ads'} active
          </Badge>
        )}
      </div>
    </header>
  );
}

// Several screens of analysis need a way to jump. Stays in view while the column scrolls.
export function SectionNav({ hasCopyVariants }: { hasCopyVariants: boolean }) {
  const sections = [
    ['diagnosis', 'Diagnosis'],
    ...(hasCopyVariants ? [['copy', 'Copy']] : []),
    ['tags', 'Tags'],
  ];
  return (
    <nav aria-label="Sections" className="sticky top-0 z-10 -mx-1 flex gap-1 overflow-x-auto bg-background/90 px-1 py-2 text-sm backdrop-blur">
      {sections.map(([id, label]) => (
        <a
          key={id}
          href={`#${id}`}
          className="rounded-md px-2.5 py-1 whitespace-nowrap text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {label}
        </a>
      ))}
    </nav>
  );
}
