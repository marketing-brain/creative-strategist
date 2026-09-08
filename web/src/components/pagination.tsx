import { ChevronLeft, ChevronRight } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';
import { cn } from 'cn';

function pageNumbers(page: number, totalPages: number): (number | 'ellipsis')[] {
  const pinned = new Set([1, totalPages, page - 1, page, page + 1].filter((p) => p >= 1 && p <= totalPages));
  const sorted = [...pinned].sort((a, b) => a - b);
  const out: (number | 'ellipsis')[] = [];
  for (let i = 0; i < sorted.length; i++) {
    if (i > 0 && sorted[i] - sorted[i - 1] > 1) out.push('ellipsis');
    out.push(sorted[i]);
  }
  return out;
}

export function Pagination({
  page,
  totalPages,
  makeHref,
}: {
  page: number;
  totalPages: number;
  makeHref: (page: number) => string;
}) {
  if (totalPages <= 1) return null;

  const iconLink = (disabled: boolean) =>
    cn(buttonVariants({ variant: 'outline', size: 'icon-sm' }), disabled && 'pointer-events-none opacity-50');

  return (
    <nav aria-label="Pagination" className="flex items-center justify-center gap-1 pt-2">
      <a href={page > 1 ? makeHref(page - 1) : undefined} aria-label="Previous page" aria-disabled={page <= 1} className={iconLink(page <= 1)}>
        <ChevronLeft aria-hidden="true" />
      </a>
      {pageNumbers(page, totalPages).map((p, i) =>
        p === 'ellipsis' ? (
          <span key={`e${i}`} className="px-1.5 text-sm text-muted-foreground">
            …
          </span>
        ) : (
          <a
            key={p}
            href={makeHref(p)}
            aria-label={`Page ${p}`}
            aria-current={p === page ? 'page' : undefined}
            className={cn(buttonVariants({ variant: p === page ? 'default' : 'ghost', size: 'icon-sm' }))}
          >
            {p}
          </a>
        )
      )}
      <a
        href={page < totalPages ? makeHref(page + 1) : undefined}
        aria-label="Next page"
        aria-disabled={page >= totalPages}
        className={iconLink(page >= totalPages)}
      >
        <ChevronRight aria-hidden="true" />
      </a>
    </nav>
  );
}
