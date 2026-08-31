import { expect, test, type Page } from '@playwright/test';

/**
 * Smoke the Claude Design wall at the authored 3840×2160 canvas.
 * Copy and layout live in the React port; this checks that the product mounts, fills the panel, and switches screens.
 */
test.use({ viewport: { width: 3840, height: 2160 } });

async function open(page: Page) {
  await page.goto('/wall.html');
  await page.waitForSelector('[data-screen-label="Command wall"]');
}

test('frame fills 3840x2160 with no scroll', async ({ page }) => {
  await open(page);
  const m = await page.evaluate(() => {
    const wall = document.querySelector('[data-screen-label="Command wall"]') as HTMLElement;
    return {
      height: Math.round(wall.getBoundingClientRect().height),
      width: Math.round(wall.getBoundingClientRect().width),
      innerH: window.innerHeight,
      innerW: window.innerWidth,
      scrollH: document.documentElement.scrollHeight,
      scrollW: document.documentElement.scrollWidth,
      root: getComputedStyle(document.documentElement).fontSize,
    };
  });
  expect(m.width).toBe(3840);
  expect(m.height).toBe(2160);
  expect(m.innerH).toBe(2160);
  expect(m.scrollH).toBe(2160);
  expect(m.scrollW).toBe(3840);
  expect(m.root).toBe('16px');
});

test('operations screen shows the product name and desks', async ({ page }) => {
  await open(page);
  await expect(page.getByText('Mobility command')).toBeVisible();
  await expect(page.getByText('ATLAS', { exact: true })).toBeVisible();
  await expect(page.getByText('AQUA', { exact: true })).toBeVisible();
  await expect(page.locator('[data-screen-label="Incident map"]')).toBeVisible();
});

test('reach band switches to evidence lineage', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Evidence lineage/ }).click();
  await expect(page.getByText('arrows read left to right')).toBeVisible();
});

test('workflow tab shows sources, agents, stakeholder, and decision', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Workflow/ }).click();
  await expect(page.locator('[data-screen-label="Workflow"]')).toBeVisible();
  await expect(page.getByText('TomTom flow')).toBeVisible();
  await expect(page.getByText('ATLAS', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Stakeholder').first()).toBeVisible();
  await expect(page.getByText('Decision').first()).toBeVisible();
});
