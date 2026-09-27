'use client';

import { useEffect } from 'react';

// Opens the <details> a link points at (#c-urgency_in_close), on load and when the hash
// changes, so a row on a long page can be shared and lands open.
export function OpenFromHash() {
  useEffect(() => {
    const open = () => {
      const id = decodeURIComponent(window.location.hash.slice(1));
      if (!id) return;
      const el = document.getElementById(id);
      const details = el?.matches('details') ? el : el?.querySelector('details');
      if (details instanceof HTMLDetailsElement) {
        details.open = true;
        el?.scrollIntoView({ block: 'start' });
      }
    };
    open();
    window.addEventListener('hashchange', open);
    return () => window.removeEventListener('hashchange', open);
  }, []);
  return null;
}
