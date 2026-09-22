import Link from 'next/link';
import { redirect } from 'next/navigation';
import { SearchX } from 'lucide-react';
import { CreativeCard, extraFor } from '@/components/creative-card';
import { RememberGridUrl } from '@/components/creative-detail-client';
import { CreativeFilters } from '@/components/creative-filters';
import { Pagination } from '@/components/pagination';
import { api } from '@/lib/api';
import { formatters, type Formatters } from '@/lib/format';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Creatives' };

const PAGE_SIZE = 50;

export default async function Creatives({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireAuth();
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);
  const ungrouped = sp.group === 'creative';
  const showAll = sp.delivery === 'all';
  const sort = sp.sort ?? 'spend';
  const search = sp.q?.trim() || undefined;

  // One builder for the page's own links and the API query, so a filter can never be on
  // screen but missing from page 2. `over` replaces or, with null, drops a parameter.
  const params = (over: Record<string, string | null> = {}) => {
    const q = new URLSearchParams();
    if (sp.account_id) q.set('account_id', sp.account_id);
    if (sp.format) q.set('format', sp.format);
    // GET /creatives 422s unless dimension and value arrive together; a dimension on its
    // own only selects which value dropdown to fetch.
    if (sp.dimension && sp.value) {
      q.set('dimension', sp.dimension);
      q.set('value', sp.value);
    }
    if (search) q.set('q', search);
    if (sp.sort) q.set('sort', sp.sort);
    if (sp.order) q.set('order', sp.order);
    if (ungrouped) q.set('group', 'creative');
    if (showAll) q.set('delivery', 'all');
    for (const [k, v] of Object.entries(over)) {
      if (v === null) q.delete(k);
      else q.set(k, v);
    }
    return q;
  };
  const href = (q: URLSearchParams) => {
    const s = q.toString();
    return `/creatives${s ? `?${s}` : ''}`;
  };
  const makeHref = (p: number) => href(params(p > 1 ? { page: String(p) } : {}));
  const apiQuery = params({
    // Cost per purchase reads cheapest first unless the reader flips it.
    order: sp.order ?? (sort === 'cpa' ? 'asc' : 'desc'),
    limit: String(PAGE_SIZE),
    offset: String((page - 1) * PAGE_SIZE),
  });

  const [list, accounts, dimensions] = await Promise.all([api.creatives(`?${apiQuery}`), api.accounts(), api.dimensions()]);
  const { items: creatives, total } = list;
  const selectedDim = dimensions.find((d) => d.name === sp.dimension);
  const openValues = selectedDim?.kind === 'open' ? await api.dimensionValues(selectedDim.name) : undefined;

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (page > totalPages) redirect(makeHref(totalPages));

  // Money is shown in each card's own account currency, never an assumed one.
  const currencyOf = new Map(accounts.map((a) => [a.id, a.currency]));
  const fmts = new Map<string, Formatters>();
  const fmtFor = (accountId: string) => {
    const currency = currencyOf.get(accountId) ?? 'USD';
    if (!fmts.has(currency)) fmts.set(currency, formatters(currency));
    return fmts.get(currency)!;
  };

  const all = list.total_all ?? total;
  const active = list.total_active ?? total;
  const idle = all - active;
  const noun = (n: number) => (ungrouped ? (n === 1 ? 'Meta creative' : 'Meta creatives') : n === 1 ? 'creative' : 'creatives');
  const count = (n: number) => n.toLocaleString('en-US');
  const filtered = !!(sp.account_id || sp.format || (sp.dimension && sp.value) || search);
  // The 30 days end on the account's newest day with delivery, not today, so name them.
  const day = (iso: string) =>
    new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const span = list.window ? `${day(list.window.start)} – ${day(list.window.end)}` : 'Last 30 days';

  return (
    <div className="space-y-4">
      <RememberGridUrl />
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Creatives</h1>
        {/* The one place the window is named: every number below covers these days. */}
        <p className="shrink-0 text-sm text-muted-foreground sm:pt-1.5">
          {list.window && <span className="mr-2 font-medium text-foreground">30d</span>}
          <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium tabular-nums">{span}</span>
        </p>
      </div>
      <CreativeFilters accounts={accounts} dimensions={dimensions} openValues={openValues} />
      {/* What the grid is showing and what it is leaving out, with the switch beside the
          count it changes. */}
      <p className="text-sm text-muted-foreground" aria-live="polite">
        {showAll ? (
          <>
            <span className="font-medium text-foreground tabular-nums">
              {count(all)} {noun(all)}
            </span>
            {idle > 0 && (
              <>
                , {count(idle)} with no delivery.{' '}
                <Link href={href(params({ delivery: null, page: null }))} className="font-medium text-foreground underline underline-offset-4 hover:no-underline">
                  Hide those
                </Link>
              </>
            )}
          </>
        ) : (
          <>
            <span className="font-medium text-foreground tabular-nums">
              {count(active)} {noun(active)}
            </span>{' '}
            with delivery.
            {idle > 0 && (
              <>
                {' '}
                <Link href={href(params({ delivery: 'all', page: null }))} className="font-medium text-foreground underline underline-offset-4 hover:no-underline">
                  Show {count(idle)} more with none
                </Link>
              </>
            )}
          </>
        )}{' '}
        <span className="hidden lg:inline">Percentages are the gap to the rest of the account, same format.</span>
      </p>
      {creatives.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-20 text-center">
          <SearchX className="size-8 text-muted-foreground" aria-hidden="true" />
          <p className="max-w-sm text-sm text-muted-foreground">
            {filtered
              ? 'No creatives match these filters.'
              : all > 0
                ? 'Nothing has delivered in this window.'
                : 'No creatives yet. They will appear here once an account finishes syncing.'}
          </p>
          {filtered && (
            <Link href="/creatives" className="text-sm font-medium underline underline-offset-4 hover:no-underline">
              Clear filters
            </Link>
          )}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {creatives.map((c, i) => (
              <CreativeCard key={c.id} creative={c} fmt={fmtFor(c.ad_account_id)} extra={extraFor(sort, c.format)} ungrouped={ungrouped} eager={i < 10} />
            ))}
          </div>
          <Pagination page={page} totalPages={totalPages} makeHref={makeHref} />
        </>
      )}
    </div>
  );
}
