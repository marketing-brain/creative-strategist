// Same shape as a video's Make page: a small player beside the verdict and the finding, the
// timeline across the page, then the first open group of ideas.
export default function Loading() {
  return (
    <div className="max-w-6xl space-y-6" aria-busy="true" aria-label="Loading">
      <div className="h-4 w-16 animate-pulse rounded bg-muted" />
      <div className="space-y-8">
        <div className="grid gap-6 sm:grid-cols-[180px_minmax(0,1fr)] sm:gap-8">
          <div className="aspect-[9/16] w-full animate-pulse rounded-xl bg-muted" />
          <div className="space-y-6">
            <div className="space-y-3">
              <div className="h-8 w-64 animate-pulse rounded-md bg-muted" />
              <div className="h-6 w-full max-w-lg animate-pulse rounded bg-muted" />
            </div>
            <div className="h-40 animate-pulse rounded-xl bg-muted" />
          </div>
        </div>
        <div className="h-36 animate-pulse rounded-xl bg-muted" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => <div key={i} className="h-72 animate-pulse rounded-xl bg-muted" />)}
        </div>
      </div>
    </div>
  );
}
