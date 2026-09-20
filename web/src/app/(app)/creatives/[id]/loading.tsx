// Mirrors the page's shape so nothing jumps when the data lands: header, stat strip, media
// column, analysis column.
const Block = ({ className }: { className: string }) => <div className={`animate-pulse rounded-xl bg-muted motion-reduce:animate-none ${className}`} />;

export default function Loading() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading creative…">
      <div className="space-y-4">
        <Block className="h-4 w-24" />
        <Block className="h-8 w-2/3 max-w-xl" />
      </div>
      <Block className="h-44 w-full" />
      <div className="grid gap-8 lg:grid-cols-3">
        <Block className="aspect-[4/5] max-h-[62dvh] w-full" />
        <div className="space-y-8 lg:col-span-2">
          <Block className="h-28 w-full" />
          <Block className="h-96 w-full" />
          <Block className="h-56 w-full" />
        </div>
      </div>
    </div>
  );
}
