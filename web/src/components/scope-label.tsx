// Which videos and days a section's numbers cover, so two sections on one page that measure
// different windows (last 30 days vs lifetime) are never read as the same numbers.
export function ScopeLabel({ children }: { children: React.ReactNode }) {
  return <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap text-muted-foreground tabular-nums">{children}</span>;
}
