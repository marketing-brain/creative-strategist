import type { LibraryCounts, LibraryDelivery } from '@/lib/types';

// A node is shown when it holds at least one ad the current delivery filter would list, so
// the tree and the grid beside it never disagree about what exists.
export const inView = (n: LibraryCounts, delivery: LibraryDelivery) =>
  delivery === 'all' ? n.ads > 0 : delivery === 'live' ? n.ads_live > 0 : n.ads_spent > 0;

export const campaignName = (c: { id: string | null; name: string | null }) => c.name ?? (c.id ? `Campaign ${c.id}` : 'No campaign');
export const adsetName = (s: { id: string | null; name: string | null }) => s.name ?? (s.id ? `Ad set ${s.id}` : 'No ad set');
