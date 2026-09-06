import { expect, test } from '@playwright/test';

// Requires: `CS_FAKE_CONNECTOR=1 make api` and `CS_FAKE_CONNECTOR=1 make worker` running, and an ad_account row for the
// dev org inserted by `scripts/seed_dev_account.py` (see Step 4). NEXT_PUBLIC_DEV_ORG must match the seeded org.
test('onboarding -> creatives -> generate -> review', async ({ page }) => {
  await page.goto('/onboarding');
  await page.getByRole('button', { name: 'Activate' }).first().click();
  await expect(page.getByText(/backfill queued/i)).toBeVisible();

  await page.getByLabel(/colors/i).fill('#112233');
  await page.getByLabel(/voice/i).fill('Warm and direct.');
  await page.getByRole('button', { name: /save brand kit/i }).click();
  await expect(page.getByText(/saved version/i)).toBeVisible();

  await expect.poll(async () => { await page.goto('/creatives'); return page.locator('a[href^="/creatives/"]').count(); }, { timeout: 60_000 }).toBeGreaterThan(0);

  await page.goto('/generate');
  await page.getByLabel(/concept/i).fill('Morning routine');
  await page.getByRole('button', { name: 'Generate' }).click();
  await expect(page.getByText(/generation queued/i)).toBeVisible();

  await expect.poll(async () => { await page.goto('/review'); return page.getByRole('button', { name: 'Approve' }).count(); }, { timeout: 90_000 }).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Approve' }).first().click();
  await expect(page.getByText('approved').first()).toBeVisible();
});
