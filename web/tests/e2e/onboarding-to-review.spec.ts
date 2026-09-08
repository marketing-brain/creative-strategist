import { expect, test } from '@playwright/test';

// Requires: `make api` and `make worker` running, a fixture meta_connection +
// Fivetran-shaped schema for the dev org inserted by `scripts/seed_dev_fivetran.py`,
// and a real, signed-in Clerk session for that org (no dev-mode auth bypass exists) --
// the latter isn't wired up yet; see README.md's Known limitations.
test('onboarding -> creatives -> generate -> review', async ({ page }) => {
  await page.goto('/onboarding');
  await page.getByRole('button', { name: 'Activate' }).first().click();
  await expect(page.getByText(/sync queued/i)).toBeVisible();

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
