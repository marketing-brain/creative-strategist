import Link from 'next/link';
import { cn } from 'cn';

// Ads is one place with three views of the same ads: by video (the library), by how Meta
// structures them (campaign, ad set, ad), and by cluster (who carries the video and when the
// product first appears).
const VIEWS = [
  { href: '/creatives', label: 'By video' },
  { href: '/campaigns', label: 'By campaign' },
  { href: '/clusters', label: 'By cluster' },
] as const;

export function AdsViews({ current }: { current: (typeof VIEWS)[number]['href'] }) {
  return (
    <nav aria-label="Ads view" className="inline-flex h-8 items-center rounded-lg bg-muted p-[3px] text-sm font-medium">
      {VIEWS.map((v) => (
        <Link
          key={v.href}
          href={v.href}
          aria-current={current === v.href ? 'page' : undefined}
          className={cn(
            'inline-flex h-full items-center rounded-md px-3 transition-colors',
            current === v.href
              ? 'bg-background text-foreground shadow-sm dark:border dark:border-input dark:bg-input/30'
              : 'text-foreground/60 hover:text-foreground dark:text-muted-foreground dark:hover:text-foreground',
          )}
        >
          {v.label}
        </Link>
      ))}
    </nav>
  );
}
