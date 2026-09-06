'use client';

// Replaces the root layout when the error happens above it (e.g. in layout.tsx
// itself), so it must render its own <html>/<body>. Deliberately dependency-free:
// globals.css is part of the layout this boundary is replacing.
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, minHeight: '100vh', fontFamily: 'system-ui, sans-serif' }}>
        <main style={{ padding: '2rem', display: 'grid', gap: '0.75rem', maxWidth: '32rem' }}>
          <h1 style={{ fontSize: '1.125rem', fontWeight: 600, margin: 0 }}>Something went wrong</h1>
          <p style={{ fontSize: '0.875rem', opacity: 0.7, margin: 0 }}>
            The app failed to start. Check that the API is running, then try again.
          </p>
          {error.digest && (
            <p style={{ fontSize: '0.75rem', opacity: 0.7, margin: 0 }}>Reference: {error.digest}</p>
          )}
          <button
            onClick={reset}
            style={{ justifySelf: 'start', padding: '0.5rem 0.75rem', borderRadius: '0.375rem', border: '1px solid currentColor', background: 'transparent', cursor: 'pointer', font: 'inherit' }}
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
