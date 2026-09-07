# Drop Direct-Publish-to-Meta; Add Download Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the direct-publish-to-Meta feature (write scopes, `publish_job`, the publish UI) and replace it with a download-the-asset-and-publish-yourself action, so nothing in the app requires Meta's `ads_management`/`business_management` write permissions.

**Architecture:** Delete the `publish_job` table and the `cs.publish` module, strip the write methods (`upload_image`, `upload_video`, `create_ad`) and the campaign/ad-set lookup methods (`list_campaigns`, `list_adsets` — they existed only to populate the publish dialog's pickers) from `AdPlatformConnector` and its implementations, remove the publish routes and the now-dead `/accounts/{id}/campaigns` and `/accounts/{id}/adsets` routes, and replace the review page's Publish button with a Download link (reusing the asset URL the API already returns) plus a Copy-details button.

**Tech Stack:** FastAPI, asyncpg, dbmate migrations, pytest, Next.js, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-06-fivetran-meta-migration-design.md` (sections 1, 4 — "Removed" and "What's explicitly light for MVP")

## Global Constraints

- All Phase 0-5 constraints from `docs/superpowers/specs/2026-09-04-creative-strategist-design.md` apply (org-scoped queries, admin-gated consequential routes, etc.).
- This plan must leave the app fully working on its own — it does not depend on the Fivetran migration plan, and the Fivetran migration plan does not depend on this one.
- `db/migrations` uses dbmate: one file per change, `-- migrate:up` / `-- migrate:down` sections, filename `YYYYMMDDHHMMSS_<name>.sql`.

---

## File structure

```
db/migrations/20260906000001_drop_publish_job.sql   # DROP TABLE publish_job (reversible)
src/cs/ingest/service.py                            # remove the publish_job lookup in sync_account
src/cs/publish/                                      # deleted entirely (meta.py, jobs.py, __init__.py)
src/cs/jobs/all_handlers.py                          # remove `import cs.publish.jobs`
src/cs/api/routers/generation.py                     # remove PublishBody, publish, publish_jobs
src/cs/api/routers/meta_targets.py                    # deleted entirely
tests/api/test_meta_targets.py                          # deleted (tested the deleted router)
src/cs/api/app.py                                     # remove meta_targets import + include_router
src/cs/connectors/base.py                             # remove upload_image/upload_video/create_ad/list_campaigns/list_adsets from the Protocol
src/cs/connectors/meta/connector.py                   # remove the same methods from MetaConnector
src/cs/connectors/fakes.py                            # remove the same methods from FakeConnector
src/cs/generation/repo.py                             # trim stale publish_job mention from a docstring
tests/connectors/test_meta_publish.py                 # deleted
tests/publish/                                        # deleted (test_publish.py, __init__.py)
tests/api/test_review_publish.py                      # rewritten as a pure review-transitions test
tests/api/test_generation.py                          # remove test_member_cannot_publish_generated_asset
tests/generation/test_service.py                      # trim stale publish_job mention from a docstring
tests/e2e/test_loop.py                                 # replace the approve+publish+resync tail with an approve+download-ready assertion
web/src/components/publish-dialog.tsx                 # deleted
web/tests/components/publish-dialog.test.tsx           # deleted
web/src/components/review-card.tsx                     # Publish button -> Download link + Copy-details button
web/tests/components/review-card.test.tsx              # new
web/src/app/(app)/review/page.tsx                       # drop accounts/jobs/isAdmin, the "Publish jobs" section
web/src/lib/api.ts                                      # remove publish, publishJobs, campaigns, adsets
web/src/lib/types.ts                                    # remove PublishJob
```

---

### Task 1: Drop `publish_job`; unhook ingest's reference to it

**Files:**
- Create: `db/migrations/20260906000001_drop_publish_job.sql`
- Modify: `src/cs/ingest/service.py:83-95`
- Test: existing `tests/ingest/test_jobs.py` / `tests/ingest/test_repo.py` (no new test file — this task is verified by the existing ingest test suite continuing to pass)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ad_account`/`creative`/`ad` ingest keeps working with no `publish_job` table in the schema. Later tasks (2, 3) can now delete the `cs.publish` module and its routes without leaving `service.py` referencing a dropped table.

- [ ] **Step 1: Write the migration**

```sql
-- migrate:up
DROP TABLE publish_job;

-- migrate:down
CREATE TABLE publish_job (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  generated_asset_id UUID NOT NULL REFERENCES generated_asset(id),
  ad_account_id UUID NOT NULL REFERENCES ad_account(id),
  campaign_id TEXT NOT NULL,
  adset_id TEXT NOT NULL,
  ad_status_requested TEXT NOT NULL CHECK (ad_status_requested IN ('PAUSED', 'ACTIVE')),
  form JSONB NOT NULL,
  platform_creative_id TEXT,
  platform_ad_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE INDEX publish_job_platform_creative_idx ON publish_job (org_id, platform_creative_id);
```

Save this as `db/migrations/20260906000001_drop_publish_job.sql`.

- [ ] **Step 2: Apply it and confirm the table is gone**

Run: `dbmate --url "$DATABASE_URL_TEST" --no-dump-schema --migrations-dir db/migrations up`
Expected: migration `20260906000001` applied; `psql "$DATABASE_URL_TEST" -c "\d publish_job"` reports the relation does not exist.

- [ ] **Step 3: Remove the now-broken query in `sync_account`**

In `src/cs/ingest/service.py`, `sync_account` currently has this block right after `creative_id, created = await repo.upsert_creative(...)`:

```python
            if created:
                ga = await ctx.db.fetchval(
                    "SELECT generated_asset_id FROM publish_job WHERE org_id=$1 "
                    "AND platform_creative_id=$2 AND status='done' LIMIT 1",
                    org_id,
                    c.platform_creative_id,
                )
                if ga:
                    await ctx.db.execute(
                        "UPDATE creative SET generated_asset_id=$2 WHERE id=$1", creative_id, ga
                    )
```

Delete it entirely (both the `if created:` block and its contents) — this was only there to link a newly-synced Meta creative back to the `generated_asset` that a completed publish had created; with publish removed there's nothing to link. The line above it, `new_creatives += int(created)`, stays.

- [ ] **Step 4: Run the ingest test suite**

Run: `pytest tests/ingest/ -v`
Expected: PASS (these tests didn't depend on the removed block, but this confirms `sync_account` still runs end-to-end against the migrated schema).

- [ ] **Step 5: Commit**

```bash
git add db/migrations/20260906000001_drop_publish_job.sql src/cs/ingest/service.py
git commit -m "feat: drop publish_job table and its ingest-side lookup"
```

---

### Task 2: Remove the publish module, routes, and write-side connector methods

**Files:**
- Delete: `src/cs/publish/meta.py`, `src/cs/publish/jobs.py`, `src/cs/publish/__init__.py`, `src/cs/api/routers/meta_targets.py`, `tests/connectors/test_meta_publish.py`, `tests/publish/test_publish.py`, `tests/publish/__init__.py`, `tests/api/test_meta_targets.py`
- Modify: `src/cs/jobs/all_handlers.py`, `src/cs/api/routers/generation.py`, `src/cs/api/app.py`, `src/cs/connectors/base.py`, `src/cs/connectors/meta/connector.py`, `src/cs/connectors/fakes.py`, `src/cs/generation/repo.py:110-113`, `tests/api/test_review_publish.py`, `tests/api/test_generation.py`, `tests/generation/test_service.py:112-115`, `tests/e2e/test_loop.py`

**Interfaces:**
- Consumes: `AdPlatformConnector` Protocol from Task 1's untouched state.
- Produces: `AdPlatformConnector` Protocol now has exactly these methods: `list_ads`, `get_creative`, `download_asset`, `fetch_metrics`. Every implementation (`MetaConnector`, `FakeConnector`) matches. No route in the app requires `ads_management`/`business_management`.

- [ ] **Step 1: Delete the publish module and its dedicated tests**

```bash
git rm -r src/cs/publish tests/connectors/test_meta_publish.py tests/publish
```

`tests/api/test_meta_targets.py` also gets deleted, but not yet — it tests `/accounts/{id}/adsets`, which Step 4 below removes. Deleting it now would leave a broken import until then; it's covered by Step 4's `git rm`.

- [ ] **Step 2: Drop the import in the handler registry**

In `src/cs/jobs/all_handlers.py`, remove the line `import cs.publish.jobs  # noqa: F401`. The file should read:

```python
"""Import every module that registers job handlers so the worker sees them."""

import cs.generation.jobs  # noqa: F401
import cs.ingest.jobs  # noqa: F401
import cs.insights.jobs  # noqa: F401
import cs.tagging.jobs  # noqa: F401
```

- [ ] **Step 3: Remove the publish routes from `generation.py`**

In `src/cs/api/routers/generation.py`, delete the `PublishBody` class and the `publish` and `publish_jobs` route functions (everything from `class PublishBody(BaseModel):` down to the end of the file). The file should end after the `reject` route function.

- [ ] **Step 4: Delete the dead campaign/ad-set router and its test**

```bash
git rm src/cs/api/routers/meta_targets.py tests/api/test_meta_targets.py
```

- [ ] **Step 5: Unregister it in `app.py`**

In `src/cs/api/app.py`, remove `meta_targets` from the import list (`from cs.api.routers import (accounts, blobs, brand, copilot, creatives, generation, health, insights, meta_oauth, meta_targets, tags)` loses `meta_targets`) and remove the line `app.include_router(meta_targets.router)`.

- [ ] **Step 6: Strip the write/lookup methods from the Protocol**

In `src/cs/connectors/base.py`, `AdPlatformConnector` currently reads:

```python
class AdPlatformConnector(Protocol):
    async def list_ads(self, account_id: str) -> list[PlatformAd]: ...
    async def get_creative(self, creative_id: str) -> PlatformCreative: ...
    async def download_asset(self, url: str) -> tuple[bytes, str]: ...
    async def fetch_metrics(self, account_id: str, since: date, until: date) -> list[MetricRow]: ...
    async def upload_image(self, account_id: str, data: bytes, name: str) -> str: ...
    async def upload_video(self, account_id: str, data: bytes, name: str) -> str: ...
    async def create_ad(
        self, account_id: str, adset_id: str, name: str, creative_spec: dict, status: str
    ) -> tuple[str, str]: ...
    async def list_campaigns(self, account_id: str) -> list[dict]: ...
    async def list_adsets(self, account_id: str, campaign_id: str) -> list[dict]: ...
```

Replace it with:

```python
class AdPlatformConnector(Protocol):
    async def list_ads(self, account_id: str) -> list[PlatformAd]: ...
    async def get_creative(self, creative_id: str) -> PlatformCreative: ...
    async def download_asset(self, url: str) -> tuple[bytes, str]: ...
    async def fetch_metrics(self, account_id: str, since: date, until: date) -> list[MetricRow]: ...
```

- [ ] **Step 7: Match it in `MetaConnector`**

In `src/cs/connectors/meta/connector.py`, delete the `upload_image`, `upload_video`, `create_ad`, `list_campaigns`, and `list_adsets` methods. Keep `list_ads`, `get_creative`, `download_asset`, `fetch_metrics` (and the `AD_FIELDS`/`CREATIVE_FIELDS`/`INSIGHT_FIELDS` constants they use) unchanged.

- [ ] **Step 8: Match it in `FakeConnector`**

In `src/cs/connectors/fakes.py`, delete the `upload_image`, `upload_video`, `create_ad`, `list_campaigns`, and `list_adsets` methods, and the `self.uploads` / `self.created_ads` attributes from `__init__` (nothing else reads them once those methods are gone).

- [ ] **Step 9: Trim the stale docstring references to `publish_job`**

In `src/cs/generation/repo.py`, `delete_generated_assets`'s docstring currently says:

```
    Scoped to exclude
    'approved' rows only: approving is the one decision that must survive a retry -- it may
    already be referenced by a publish_job (generated_asset_id has no ON DELETE clause), so
    deleting it would raise a foreign-key violation instead of a clean retry. A 'rejected' row
```

Replace the middle sentence so it reads:

```
    Scoped to exclude
    'approved' rows only: approving is the one decision that must survive a retry, and deleting
    it would silently discard that human call. A 'rejected' row
```

In `tests/generation/test_service.py`, `test_retry_preserves_already_reviewed_assets`'s docstring currently says:

```
    """A retry's cleanup must not delete a generated_asset a human already approved: doing so
    would (a) silently discard that decision, and (b) if the asset had already been
    published, violate publish_job's FK to generated_asset (asyncpg.ForeignKeyViolationError)
    since publish_job.generated_asset_id has no ON DELETE clause. Only non-approved rows
    (draft or rejected) are cleared before a retry regenerates."""
```

Replace it with:

```
    """A retry's cleanup must not delete a generated_asset a human already approved: doing so
    would silently discard that decision. Only non-approved rows (draft or rejected) are
    cleared before a retry regenerates."""
```

- [ ] **Step 10: Rewrite `tests/api/test_review_publish.py` as a pure review-transitions test**

This file currently mixes review-state coverage with publish-gate coverage. Replace its entire contents with:

```python
from cs.generation import repo
from cs.generation.brand import create_brand_kit
from tests.conftest import dev_headers

H = dev_headers(org="org_rev")


async def _asset(client, db):
    from uuid import UUID

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    kit = await create_brand_kit(db, org_id, [], [], "", [])
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u", concept="c")
    return org_id, await repo.insert_generated_asset(
        db, org_id, gid, "image", "1:1", 1, "p.jpg", "s", "H", "P", "SHOP_NOW"
    )


async def test_review_transitions(client, db):
    _, aid = await _asset(client, db)
    assert len((await client.get("/review-queue?state=draft", headers=H)).json()) == 1
    assert (await client.post(f"/generated-assets/{aid}/approve", headers=H)).json() == {"ok": True}
    assert len((await client.get("/review-queue?state=approved", headers=H)).json()) == 1
    assert (await client.post(f"/generated-assets/{aid}/reject", headers=H)).json() == {"ok": False}
```

(`approve` after `draft` succeeds — `{"ok": True}`; `reject` after `approved` is refused by the review-state machine — `{"ok": False}` — matching the state-machine behavior the original test already exercised.)

- [ ] **Step 11: Remove the publish-gate test from `test_generation.py`**

In `tests/api/test_generation.py`, delete the `test_member_cannot_publish_generated_asset` function (the last function in the file, lines 42-63). The `uuid` import at the top (`from uuid import UUID`) is also now unused — remove it too, since no remaining test in this file uses `UUID`.

- [ ] **Step 12: Truncate the publish tail of the full-loop e2e test**

`tests/e2e/test_loop.py` (`test_full_loop`) drives the whole backfill → tag → insight → generate → approve → publish → sync loop on fakes. Its final two sections (currently labeled `# 4. approve + publish` and `# 5. next sync closes the loop`) call the now-deleted `/generated-assets/{id}/publish` and `/publish-jobs` routes, and assert a `creative.generated_asset_id` link that only ever got populated via the just-deleted `publish_job` lookup in `sync_account` (Task 1, Step 3) — that whole "next sync re-discovers the published creative and links it back" behavior no longer exists. Replace both sections (everything from `# 4. approve + publish` to the end of the function) with:

```python
    # 4. approve -> asset is ready to download, with its ad copy alongside it
    aid = detail["assets"][0]["id"]
    await client.post(f"/generated-assets/{aid}/approve", headers=H)
    approved = (await client.get("/review-queue?state=approved", headers=H)).json()
    asset = next(a for a in approved if a["id"] == aid)
    assert asset["url"] and asset["headline"] and asset["primary_text"] and asset["cta_type"]
```

Also update the module docstring at the top of the file — it currently reads:

```python
"""Backfill -> tag -> insight -> generate -> approve -> publish -> sync closes the loop.

All on fakes.
"""
```

Change it to:

```python
"""Backfill -> tag -> insight -> generate -> approve, ready to download.

All on fakes.
"""
```

Leave everything before `# 4.` (the `FakeConnector`/`encrypt_str` setup, backfill, insights, generation) untouched — that's ingest machinery this plan doesn't touch. (It gets its own rewrite in the Fivetran ingest migration plan, `docs/superpowers/plans/2026-09-07-fivetran-meta-ingest.md`, Task 11 — don't fix it here.)

- [ ] **Step 13: Run the full backend test suite**

Run: `pytest tests/ -v`
Expected: PASS. No test references `publish_job`, `cs.publish`, `meta_targets`, `upload_image`, `upload_video`, `create_ad`, `list_campaigns`, or `list_adsets`.

Run: `grep -rn "publish_job\|cs\.publish\|meta_targets" src tests`
Expected: no output.

- [ ] **Step 14: Commit**

```bash
git add -A
git commit -m "feat: remove direct-publish-to-Meta (routes, protocol methods, tests)"
```

---

### Task 3: Replace the Publish button with Download + Copy-details

**Files:**
- Delete: `web/src/components/publish-dialog.tsx`, `web/tests/components/publish-dialog.test.tsx`
- Modify: `web/src/components/review-card.tsx`, `web/src/app/(app)/review/page.tsx`, `web/src/lib/api.ts`, `web/src/lib/types.ts`
- Create: `web/tests/components/review-card.test.tsx`

**Interfaces:**
- Consumes: `GeneratedAsset` type (`web/src/lib/types.ts`, unchanged shape — `url`, `headline`, `primary_text`, `cta_type` already present), `blobUrl` from `@/lib/blob-url` (unchanged).
- Produces: `ReviewCard` no longer takes `accounts`/`isAdmin` props — just `{ asset }`.

- [ ] **Step 1: Delete the publish dialog and its test**

```bash
git rm web/src/components/publish-dialog.tsx web/tests/components/publish-dialog.test.tsx
```

- [ ] **Step 2: Write the failing test for the new `ReviewCard`**

Create `web/tests/components/review-card.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ReviewCard } from '@/components/review-card';

const asset = {
  id: 'a1',
  generation_id: 'g',
  kind: 'image' as const,
  aspect: '1:1',
  variant: 1,
  url: '/blobs/org1/creatives/a1.jpg',
  headline: 'H',
  primary_text: 'P',
  cta_type: 'SHOP_NOW',
  review_state: 'approved' as const,
};

describe('ReviewCard', () => {
  it('offers a download link and copies ad copy to the clipboard for an approved asset', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    render(<ReviewCard asset={asset} />);

    const link = screen.getByRole('link', { name: /download/i });
    expect(link).toHaveAttribute('href', '/blobs/org1/creatives/a1.jpg');
    expect(link).toHaveAttribute('download');

    await userEvent.click(screen.getByRole('button', { name: /copy details/i }));
    expect(writeText).toHaveBeenCalledWith('H\n\nP\n\nCTA: SHOP_NOW');
  });

  it('shows approve/reject for a draft asset, not download', () => {
    render(<ReviewCard asset={{ ...asset, review_state: 'draft' }} />);
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /download/i })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd web && npx vitest run tests/components/review-card.test.tsx`
Expected: FAIL — `ReviewCard` still requires `accounts`/`isAdmin` props and renders a Publish button, not Download/Copy details.

- [ ] **Step 4: Rewrite `review-card.tsx`**

Replace `web/src/components/review-card.tsx` entirely with:

```tsx
'use client';
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from 'cn';
import { ApiError, api } from '@/lib/api';
import { blobUrl } from '@/lib/blob-url';
import type { GeneratedAsset } from '@/lib/types';

const stateVariant = { draft: 'secondary', approved: 'success', rejected: 'destructive' } as const;

export function ReviewCard({ asset }: { asset: GeneratedAsset }) {
  const [state, setState] = useState(asset.review_state);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const act = async (s: 'approved' | 'rejected') => {
    setError(null);
    try {
      const { ok } = await (s === 'approved' ? api.approve(asset.id) : api.reject(asset.id));
      if (ok) setState(s);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setError("You don't have permission to do this — ask an admin.");
      } else {
        setError(`Failed: ${e instanceof Error ? e.message : 'Something went wrong.'}`);
      }
    }
  };
  const copyDetails = async () => {
    await navigator.clipboard.writeText(`${asset.headline}\n\n${asset.primary_text}\n\nCTA: ${asset.cta_type}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <Card size="sm">
      {asset.kind === 'video' ? (
        <video src={blobUrl(asset.url)} controls className="aspect-square w-full object-cover" />
      ) : (
        <img src={blobUrl(asset.url)} alt={asset.headline} className="aspect-square w-full object-cover" />
      )}
      <CardContent className="space-y-2 text-sm">
        <div className="flex items-center justify-between">
          <Badge variant="outline">
            {asset.aspect} · v{asset.variant}
          </Badge>
          <Badge variant={stateVariant[state]} className="capitalize">
            {state}
          </Badge>
        </div>
        <div className="line-clamp-2 font-medium">{asset.headline}</div>
        <div className="line-clamp-2 text-muted-foreground">{asset.primary_text}</div>
        <div className="flex gap-2 pt-1">
          {state === 'draft' && (
            <>
              <Button size="sm" onClick={() => act('approved')}>
                Approve
              </Button>
              <Button size="sm" variant="outline" onClick={() => act('rejected')}>
                Reject
              </Button>
            </>
          )}
          {state === 'approved' && (
            <>
              <a href={blobUrl(asset.url)} download className={cn(buttonVariants({ size: 'sm' }))}>
                Download
              </a>
              <Button size="sm" variant="outline" onClick={copyDetails}>
                {copied ? 'Copied' : 'Copy details'}
              </Button>
            </>
          )}
        </div>
        {error && (
          <p role="status" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
```

(This app's `Button` (`web/src/components/ui/button.tsx`) wraps `base-ui`'s primitive and has no `asChild` prop, so a button-styled link is built with the exported `buttonVariants` helper instead — the same pattern `web/src/components/insight-card.tsx:44` already uses for its "Generate from this" link.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd web && npx vitest run tests/components/review-card.test.tsx`
Expected: PASS

- [ ] **Step 6: Simplify the review page**

Replace `web/src/app/(app)/review/page.tsx` entirely with:

```tsx
import { ClipboardCheck } from 'lucide-react';
import { ReviewCard } from '@/components/review-card';
import { cn } from 'cn';
import { api } from '@/lib/api';
import { requireAuth } from '@/lib/require-auth.server';

export const metadata = { title: 'Review' };

const states = ['draft', 'approved', 'rejected'] as const;

export default async function Review({ searchParams }: { searchParams: Promise<{ state?: string }> }) {
  await requireAuth();
  const { state = 'draft' } = await searchParams;
  const assets = await api.reviewQueue(state);
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
          <p className="text-sm text-muted-foreground">Approve, reject, and download generated variants.</p>
        </div>
        <div className="inline-flex w-fit gap-1 rounded-lg bg-muted p-1">
          {states.map((s) => (
            <a
              key={s}
              href={`/review?state=${s}`}
              aria-current={s === state ? 'page' : undefined}
              className={cn(
                'rounded-md px-3 py-1 text-sm font-medium capitalize transition-colors',
                s === state ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
              )}
            >
              {s}
            </a>
          ))}
        </div>
      </div>
      {assets.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-16 text-center">
          <ClipboardCheck className="size-8 text-muted-foreground" aria-hidden="true" />
          <p className="max-w-sm text-sm text-muted-foreground">Nothing in the {state} queue right now.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-4">
          {assets.map((a) => (
            <ReviewCard key={a.id} asset={a} />
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 7: Remove the dead API client functions and type**

In `web/src/lib/api.ts`, delete these four lines from the `api` object:

```ts
  publish: (id: string, b: object) => apiFetch<{ publish_job_id: string }>(`/generated-assets/${id}/publish`, { method: 'POST', body: JSON.stringify(b) }),
  publishJobs: () => apiFetch<import('./types').PublishJob[]>('/publish-jobs'),
  campaigns: (accountId: string) => apiFetch<{ id: string; name: string }[]>(`/accounts/${accountId}/campaigns`),
  adsets: (accountId: string, campaignId: string) => apiFetch<{ id: string; name: string }[]>(`/accounts/${accountId}/adsets?campaign_id=${campaignId}`),
```

In `web/src/lib/types.ts`, delete the line `export type PublishJob = { id: string; generated_asset_id: string; status: string; platform_ad_id: string | null; error: string | null; created_at: string };`.

- [ ] **Step 8: Run the full frontend test suite and typecheck**

Run: `cd web && npx vitest run && npx tsc --noEmit`
Expected: both PASS. No reference to `PublishDialog`, `api.publish`, `api.publishJobs`, `api.campaigns`, `api.adsets`, or `PublishJob` remains.

Run: `grep -rn "PublishDialog\|api\.publish\|api\.campaigns\|api\.adsets\|PublishJob" web/src web/tests`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: replace publish-to-Meta with a download-and-copy action"
```
