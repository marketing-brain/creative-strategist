// Shown the moment a link is clicked, for every page without a skeleton of its own: the page
// renders on the server, and without this the screen froze until it arrived.
export default function Loading() {
  return (
    <div className="max-w-5xl space-y-8" aria-busy="true" aria-label="Loading">
      <div className="space-y-2">
        <div className="h-8 w-48 animate-pulse rounded-md bg-muted" />
        <div className="h-4 w-96 max-w-full animate-pulse rounded-md bg-muted" />
      </div>
      <div className="h-4 w-80 max-w-full animate-pulse rounded-md bg-muted" />
      <div className="divide-y divide-border overflow-hidden rounded-xl border border-border">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex items-center gap-4 p-4">
            <div className="aspect-[4/5] w-12 animate-pulse rounded bg-muted" />
            <div className="flex-1 space-y-2">
              <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
              <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
