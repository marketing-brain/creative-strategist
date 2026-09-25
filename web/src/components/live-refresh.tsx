'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

// Re-reads the page from the server while something on it is running, so a step's progress
// moves without a reload. Stops as soon as nothing is running.
export function LiveRefresh({ active, everyMs = 3000 }: { active: boolean; everyMs?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => router.refresh(), everyMs);
    return () => clearInterval(t);
  }, [active, everyMs, router]);
  return null;
}
