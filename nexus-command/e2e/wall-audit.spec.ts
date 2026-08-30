import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 3840, height: 2160 } });

test('wall frame meets the 3840 audit', async ({ page }) => {
  await page.goto('/');
  await page.waitForSelector('.wall-frame');
  await page.waitForSelector('.wall-hero__situation');
  await page.waitForTimeout(800);

  const frame = await page.evaluate(() => ({
    app: Math.round(document.getElementById('app')?.getBoundingClientRect().height ?? 0),
    inner: window.innerHeight,
    width: window.innerWidth,
  }));
  expect(frame.app).toBe(2160);
  expect(frame.inner).toBe(2160);
  expect(frame.width).toBe(3840);

  const columns = await page.evaluate(() => {
    const body = document.querySelector('.wall-body') as HTMLElement | null;
    const map = document.querySelector('.wall-map') as HTMLElement | null;
    const stack = document.querySelector('.wall-stack') as HTMLElement | null;
    return {
      map: map ? Math.round(map.getBoundingClientRect().width) : 0,
      stack: stack ? Math.round(stack.getBoundingClientRect().width) : 0,
      template: body ? getComputedStyle(body).gridTemplateColumns : '',
    };
  });
  expect(columns.map).toBe(2000);
  expect(columns.stack).toBeGreaterThanOrEqual(1600);

  const heroButtons = await page.locator('.wall-hero button').count();
  expect(heroButtons).toBe(0);

  const situationLines = await page.evaluate(() => {
    const hero = document.querySelector('.wall-hero__situation') as HTMLElement | null;
    const sub = document.querySelector('.wall-hero__subtitle') as HTMLElement | null;
    if (!hero || !sub) return { situation: 0, subtitle: 0 };
    const line = (el: HTMLElement) => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight));
    return { situation: line(hero), subtitle: line(sub) };
  });
  expect(situationLines.situation).toBeLessThanOrEqual(2);
  expect(situationLines.subtitle).toBeLessThanOrEqual(1);

  const monoLetters = await page.evaluate(() => (
    [...document.querySelectorAll('.wall-figure')].filter(element => /[A-Za-z]/.test(element.textContent ?? ''))
      .map(element => element.textContent)
  ));
  expect(monoLetters).toEqual([]);

  const badSizes = await page.evaluate(() => {
    const allowed = new Set([28, 36, 48, 64, 96, 128]);
    const leaves: Element[] = [];
    const walk = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE && node.textContent?.trim()) {
        const el = node.parentElement;
        if (el?.closest('.wall-frame') && !el.closest('.maplibregl-map')) leaves.push(el);
      } else if (node.nodeType === Node.ELEMENT_NODE) node.childNodes.forEach(walk);
    };
    walk(document.querySelector('.wall-frame')!);
    return [...new Set(leaves)].filter(element => {
      const size = Math.round(parseFloat(getComputedStyle(element).fontSize));
      return !allowed.has(size);
    }).map(element => `${element.tagName}.${element.className} ${getComputedStyle(element).fontSize} ${element.textContent?.slice(0, 24)}`);
  });
  expect(badSizes).toEqual([]);

  const rail = await page.evaluate(() => {
    const status = document.querySelector('.wall-status') as HTMLElement | null;
    const navs = [...document.querySelectorAll('.wall-nav')];
    const segments = [...document.querySelectorAll('.wall-nav button')];
    return {
      height: status ? Math.round(status.getBoundingClientRect().height) : 0,
      clusters: navs.length,
      segmentHeight: segments[0] ? Math.round(segments[0].getBoundingClientRect().height) : 0,
    };
  });
  expect(rail.height).toBe(304);
  expect(rail.clusters).toBe(2);
  expect(rail.segmentHeight).toBe(200);

  const agents = await page.evaluate(() => {
    const tiles = [...document.querySelectorAll('.agent-tile')];
    const wrapped = tiles.flatMap(tile => (
      [...tile.querySelectorAll('span')].filter(span => span.getClientRects().length !== 1)
        .map(span => span.textContent)
    ));
    return {
      count: tiles.length,
      aboveReach: tiles.filter(tile => tile.getBoundingClientRect().top < 1080).length,
      wrapped,
    };
  });
  expect(agents.count).toBe(6);
  expect(agents.aboveReach).toBe(0);
  expect(agents.wrapped).toEqual([]);

  const impact = await page.evaluate(() => (
    [...document.querySelectorAll('.wall-impact__cell')].map(cell => ({
      hasUnit: /vehicles|rows|closures/.test(cell.textContent ?? ''),
    }))
  ));
  expect(impact).toHaveLength(3);
  expect(impact.every(cell => cell.hasUnit)).toBe(true);

  const bar = await page.evaluate(() => ({
    pills: document.querySelectorAll('.wall-ticker').length,
    attrInBar: Boolean(document.querySelector('.wall-top .wall-attr, .wall-top .wall-map__attr')),
    attrInMap: Boolean(document.querySelector('.wall-map .wall-map__attr')),
    rightKids: document.querySelector('.wall-top__right')?.childElementCount,
  }));
  expect(bar.pills).toBe(0);
  expect(bar.attrInBar).toBe(false);
  expect(bar.attrInMap).toBe(true);
  expect(bar.rightKids).toBe(3);

  const railControls = await page.evaluate(() => (
    [...document.querySelectorAll('.wall-nav button, .wall-rail-actions .wall-button')].map(el => ({
      h: Math.round(el.getBoundingClientRect().height),
      bg: getComputedStyle(el).backgroundColor,
    }))
  ));
  expect(railControls.every(item => item.h === 200)).toBe(true);

  await page.locator('.wall-nav button', { hasText: 'Lineage' }).first().dispatchEvent('pointerdown');
  await page.waitForSelector('.wall-funnel');
  const lineage = await page.evaluate(() => ({
    app: Math.round(document.getElementById('app')?.getBoundingClientRect().height ?? 0),
    inner: window.innerHeight,
  }));
  expect(lineage.app).toBe(2160);
  expect(lineage.inner).toBe(2160);
});
