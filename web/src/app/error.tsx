'use client';
import { Button } from '@/components/ui/button';

// Route-level error boundary: every page here is an async server component that
// calls the API unguarded, so a backend outage would otherwise show Next's
// default error screen.
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="space-y-3 rounded border p-6">
      <h1 className="text-lg font-semibold">Something went wrong</h1>
      <p className="text-sm text-muted-foreground">
        We couldn&apos;t load this page. The API may be unavailable — check that it is running, then try again.
      </p>
      {error.digest && <p className="text-xs text-muted-foreground">Reference: {error.digest}</p>}
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
