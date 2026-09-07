'use client';
import { useEffect } from 'react';

// The Fivetran Connect Card redirects here (inside the popup) once the user finishes.
// Notify the opener so it can finalize, then close the popup. No auth required — this
// page carries no data of its own, it's just a signal back to the tab that opened it.
export default function FivetranReturn() {
  useEffect(() => {
    try {
      window.opener?.postMessage({ type: 'fivetran:return' }, window.location.origin);
    } finally {
      window.close();
    }
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
      You can close this window.
    </main>
  );
}
