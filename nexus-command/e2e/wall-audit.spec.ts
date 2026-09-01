import { expect, test, type Page } from '@playwright/test';

/**
 * Smoke the Civic Instrument Panel wall at the authored 3840×2160 canvas.
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
  await expect(page.getByText('Mobility command', { exact: true })).toBeVisible();
  await expect(page.getByText('Nexus Coordinate', { exact: true })).toBeVisible();
  await expect(page.locator('.nx-wall-identity')).toBeVisible();
  await expect(page.getByText('ATLAS', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('AQUA', { exact: true }).first()).toBeVisible();
  await expect(page.locator('[data-screen-label="Incident map"]')).toBeVisible();
  const notice = await page.locator('[data-screen-label="Priority card"]').boundingBox();
  expect(notice).not.toBeNull();
  expect(notice!.width).toBeLessThan(800);
  expect(notice!.height).toBeLessThan(420);
});

test('real Auburn–Opelika map exposes six agent facility markers', async ({ page }) => {
  await open(page);
  const markers = page.locator('.nx-agent-marker-shell');
  await expect(markers).toHaveCount(6);
  const echo = page.locator('.nx-agent-marker-shell[title^="ECHO"]');
  await echo.click();
  await expect(page.getByText('Lee County Emergency Management Agency', { exact: true })).toBeVisible();
  await expect(page.getByText('908 Avenue B, Opelika, AL 36801', { exact: true })).toBeVisible();
});

test('reach band switches to evidence lineage', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Evidence lineage/ }).click();
  await expect(page.getByText('arrows read left to right')).toBeVisible();
  await expect(page.locator('.nx-wall-brandrow > .nx-wall-mark')).toBeHidden();
  const heading = await page.locator('.nx-wall-evidence__header h1').boundingBox();
  const subtitle = await page.locator('.nx-wall-evidence__header p').boundingBox();
  const context = await page.locator('.nx-wall-evidence__header strong').boundingBox();
  expect(heading && subtitle && context).toBeTruthy();
  expect(heading!.y).toBeLessThan(subtitle!.y);
  expect(subtitle!.y).toBeLessThan(context!.y);
  await expect(page.getByLabel('Evidence lineage Sankey diagram')).toBeVisible();
  const body = await page.locator('.nx-wall-evidence__body').boundingBox();
  const sankey = await page.getByLabel('Evidence lineage Sankey diagram').boundingBox();
  expect(body && sankey).toBeTruthy();
  expect(sankey!.width / body!.width).toBeGreaterThan(0.98);
  await expect(page.locator('.nx-sankey__stage')).toHaveCount(7);
  expect(await page.locator('[data-sankey-node]').count()).toBeGreaterThanOrEqual(7);
  expect(await page.locator('[data-sankey-link]').count()).toBeGreaterThanOrEqual(4);
  await page.locator('[data-sankey-node][data-stage="decision"]').click();
  await expect(page.getByText('Decision text', { exact: true })).toBeVisible();
  await expect(page.getByText('Stakeholder / source', { exact: true })).toBeVisible();
  await expect(page.getByText('Citation inputs', { exact: true })).toBeVisible();
  const inspector = await page.locator('.nx-wall-lineage__inspector').boundingBox();
  expect(inspector).not.toBeNull();
  expect(inspector!.width).toBeLessThan(600);
  expect(inspector!.height).toBeLessThan(600);
  await page.locator('[data-sankey-node="c-none"]').click();
  expect(await page.locator('.nx-sankey__links path.is-dimmed').count()).toBeGreaterThan(0);
});

test('Stage 2 differentiates agents, desks, findings, and participation without truncation', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Deliberation/ }).click();
  await expect(page.getByLabel('Auburn University Harbert Business')).toBeVisible();
  await expect(page.locator('.nx-delib-agent img')).toHaveCount(6);
  await expect(page.locator('.nx-delib-desk img')).toHaveCount(6);
  await expect(page.locator('.nxw-agent-row--contributed .nx-delib-status').first()).toBeVisible();
  await expect(page.locator('.nxw-agent-row--abstained .nx-delib-status').first()).toBeVisible();
  await expect(page.locator('.nx-delib-summary__detail--dissent')).toBeVisible();
  const overflow = await page.locator('.nx-delib-action').first().evaluate(el => getComputedStyle(el).textOverflow);
  expect(overflow).toBe('clip');
});

test('Stage 4 explains the decision, effect, limitation, stakeholders, and signing record in one contained view', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /The decision/ }).click();
  const workspace = page.locator('[data-screen-label="Decision workspace"]');
  await expect(workspace).toBeVisible();
  await expect(page.getByText('What must be resolved', { exact: true })).toBeVisible();
  await expect(page.getByText('What follows if approved', { exact: true })).toBeVisible();
  await expect(page.getByText('What this decision cannot do', { exact: true })).toBeVisible();
  await expect(page.getByText('Who must decide', { exact: true })).toBeVisible();
  await expect(page.getByText('How accountability is recorded', { exact: true })).toBeVisible();
  await expect(page.locator('.nx-decision-card__icon svg')).toHaveCount(4);
  const contained = await workspace.evaluate(el => el.scrollHeight <= el.clientHeight + 1 && el.scrollWidth <= el.clientWidth + 1);
  expect(contained).toBe(true);
});

test('Workflow provides branded connectors, a complete draggable catalog, and drawable connections', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Workflow/ }).click();
  await expect(page.locator('[data-screen-label="Workflow"]')).toBeVisible();
  for (const connector of ['Box', 'Google Drive', 'Microsoft SharePoint']) {
    await expect(page.locator(`[data-workflow-node="${connector}"]`)).toBeVisible();
  }
  await expect(page.getByText('TomTom flow').first()).toBeVisible();
  await expect(page.getByText('ATLAS', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Stakeholder').first()).toBeVisible();
  await expect(page.getByText('Decision record', { exact: true })).toBeVisible();

  const palette = page.locator('[data-workflow-palette]');
  await expect(palette).toHaveCount(21);
  expect(await palette.evaluateAll(items => items.every(item => item.getAttribute('draggable') === 'true'))).toBe(true);

  const boxNode = page.locator('[data-workflow-node="Box"]').locator('xpath=..');
  const before = await boxNode.boundingBox();
  expect(before).not.toBeNull();
  await page.mouse.move(before!.x + before!.width / 2, before!.y + before!.height / 2);
  await page.mouse.down();
  await page.mouse.move(before!.x + before!.width / 2 + 100, before!.y + before!.height / 2 + 70, { steps: 14 });
  await page.mouse.up();
  const after = await boxNode.boundingBox();
  expect(after).not.toBeNull();
  expect(Math.abs(after!.x - before!.x) + Math.abs(after!.y - before!.y)).toBeGreaterThan(20);

  const edgeCount = await page.locator('.react-flow__edge').count();
  const source = await page.locator('[data-workflow-node="Box"] .react-flow__handle-right').boundingBox();
  const target = await page.locator('[data-workflow-node="ECHO"] .react-flow__handle-left').boundingBox();
  expect(source && target).toBeTruthy();
  await page.mouse.move(source!.x + source!.width / 2, source!.y + source!.height / 2);
  await page.mouse.down();
  await page.mouse.move(target!.x + target!.width / 2, target!.y + target!.height / 2, { steps: 16 });
  await page.mouse.up();
  await expect.poll(() => page.locator('.react-flow__edge').count()).toBe(edgeCount + 1);

  await page.getByRole('button', { name: 'Reset', exact: true }).click();
  await expect(page.locator('.react-flow__edge')).toHaveCount(edgeCount);
});

test('desk tiles and screen tabs are press targets', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: 'Open ATLAS' }).click();
  await expect(page.getByText(/DESK ATLAS/i)).toBeVisible();
  await page.getByRole('button', { name: 'CANCEL' }).click();
  for (const name of [/Deliberation/, /Evidence lineage/, /The decision/, /Commitments/, /Workflow/, /Operations/]) {
    await page.getByRole('button', { name }).click();
  }
  await expect(page.locator('[data-screen-label="Incident map"]')).toBeVisible();
});

test('all six Agent Desks open full-screen with shared structured configuration', async ({ page }) => {
  await open(page);
  const desks = ['ATLAS', 'AQUA', 'SENTINEL', 'PHOENIX', 'FORGE', 'ECHO'];
  for (const desk of desks) {
    await page.getByRole('button', { name: `Open ${desk}` }).click();
    const dialog = page.getByRole('dialog', { name: `${desk.toLowerCase()} Agent Desk` });
    await expect(dialog).toBeVisible();
    const bounds = await dialog.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.y).toBe(0);
    expect(bounds!.height).toBe(2160);
    await expect(page.locator('.nx-wall-header')).toBeHidden();

    await page.getByRole('button', { name: /Prompt/ }).click();
    await expect(page.getByRole('heading', { name: 'Instruction design' })).toBeVisible();

    await page.getByRole('button', { name: /Model/ }).click();
    await expect(page.getByRole('heading', { name: desk === 'ATLAS' || desk === 'AQUA' ? 'Runtime configuration' : 'Managed runtime' })).toBeVisible();

    if (desk === 'ATLAS' || desk === 'AQUA') {
      await page.getByRole('button', { name: /Tools/ }).click();
      await expect(page.getByRole('heading', { name: 'Capabilities and access' })).toBeVisible();
      await page.getByRole('button', { name: /Policies/ }).click();
      await expect(page.getByRole('heading', { name: 'Guardrails and authority' })).toBeVisible();
    }

    const collapse = page.getByRole('button', { name: 'Hide stages' });
    await collapse.click();
    await expect(page.getByRole('button', { name: 'Show stages' })).toHaveAttribute('aria-expanded', 'false');
    await page.getByRole('button', { name: 'CANCEL' }).click();
  }
});
