import { FileX2, HelpCircle } from 'lucide-react';
import type { FormatStatus } from '@/lib/creative-format';

export function MissingFileNotice({ status }: { status: FormatStatus }) {
  const Icon = status.kind === 'unknown' ? HelpCircle : FileX2;
  return (
    <div className="flex aspect-square flex-col items-center justify-center gap-3 px-6 text-center">
      <Icon className="size-8 text-muted-foreground" aria-hidden="true" />
      <h2 className="text-sm font-semibold">{status.label}</h2>
      {status.explanation && <p className="max-w-xs text-sm text-pretty text-muted-foreground">{status.explanation}</p>}
      <p className="max-w-xs text-xs text-pretty text-muted-foreground">
        Spend and delivery metrics still count. Tags and same-format comparisons need the file.
      </p>
    </div>
  );
}
