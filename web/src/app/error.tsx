'use client';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';

// Route-level error boundary: every page here is an async server component that
// calls the API unguarded, so a backend outage would otherwise show Next's
// default error screen.
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-3 text-center">
      <AlertTriangle className="size-8 text-destructive" aria-hidden="true" />
      <h1 className="text-lg font-semibold">Something went wrong</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        We couldn’t load this page. The API may be unavailable, check that it’s running, then try again.
      </p>
      {error.digest && <p className="text-xs text-muted-foreground">Reference: {error.digest}</p>}
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
