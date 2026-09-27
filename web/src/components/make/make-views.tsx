import Link from 'next/link';
import { cn } from 'cn';
import type { CreatorScriptRow } from '@/lib/types';

// Make's two starting points, one page each: improve a video you run (/make), or make a brand-new
// one of a kind that works (/new-video).
const VIEWS = [
  { key: 'improve', href: '/make', label: 'Improve a video' },
  { key: 'new', href: '/new-video', label: 'New video for a kind' },
] as const;

export function MakeViews({ current }: { current: (typeof VIEWS)[number]['key'] }) {
  return (
    <nav aria-label="Make view" className="inline-flex h-8 items-center rounded-lg bg-muted p-[3px] text-sm font-medium">
      {VIEWS.map((v) => (
        <Link
          key={v.key}
          href={v.href}
          aria-current={current === v.key ? 'page' : undefined}
          className={cn(
            'inline-flex h-full items-center rounded-md px-3 transition-colors',
            current === v.key
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


export function KindStatus({ k }: { k: CreatorScriptRow }) {
  const v = k.video;
  const [label, cls] = v?.experiment_id
    ? ['Launched', 'border-success/40 text-success']
    : v?.status === 'done'
      ? ['Made', 'border-success/40 text-success']
      : v?.status === 'confirm'
        ? ['Waiting for you', 'border-primary/40 text-primary']
        : v?.status === 'queued' || v?.status === 'running'
          ? ['Making', 'border-primary/40 text-primary']
          : v?.status === 'blocked' || v?.status === 'failed'
            ? ['Stopped', 'border-destructive/40 text-destructive']
            : k.script
              ? ['Script written', 'border-foreground/25 text-foreground']
              : ['Not started', 'border-border text-muted-foreground'];
  return <span className={`w-fit rounded-sm border px-1.5 py-0.5 text-[12px] leading-none font-medium whitespace-nowrap ${cls}`}>{label}</span>;
}
