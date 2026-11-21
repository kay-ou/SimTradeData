import { test, expect } from '@playwright/test';

for (const symbols of ['159919.SZ, 159920.SZ', '159919.SZ，159920.SZ', '159919.SZ 159920.SZ']) {
  test(`automatic recommendations normalize symbols: ${symbols}`, async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'ETF申赎清单', exact: true }).click();
    const responsePromise = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/recommendation' && url.searchParams.get('symbols') === '159919.SZ,159920.SZ';
    });
    await page.getByLabel('ETF代码').fill(symbols);
    const response = await responsePromise;
    expect(response.ok()).toBe(true);
    expect((await response.json()).request.symbols).toEqual(['159919.SZ', '159920.SZ']);
    await expect(page.getByRole('alert')).toHaveCount(0);
  });
}

test('one smart button fills gaps from cache and verifies the result', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page).toHaveTitle('SimTradeData · 数据管理');
  await expect(page.getByRole('heading', { name: '更新你的回测数据' })).toBeVisible();
  await expect(page.getByLabel('更新方式')).not.toBeVisible();
  await expect(page.getByLabel('自动检查结果')).toContainText('已检查本地数据');
  await expect(page.locator('.automatic-range')).toContainText('2026-11-18 至 2026-11-20');
  await expect(page.getByLabel('开始日期')).toHaveCount(0);
  await page.screenshot({ path: '/tmp/simtrade-ui-simple-desktop.png', fullPage: true });
  await page.getByRole('button', { name: '一键下载补齐', exact: true }).click();
  await expect(page.locator('.task-status')).toHaveText('已完成', { timeout: 30000 });
  await expect(page.locator('.comparison')).toContainText('1 → 3');
  await expect(page.locator('.comparison')).toContainText('2 → 0');
  await expect(page.getByRole('button', { name: '已同步，无需更新' })).toBeDisabled();
  await expect(page.getByRole('heading', { name: '更新记录' })).toHaveCount(0);
  await page.getByRole('button', { name: '查看本地数据详情' }).click();
  await expect(page.locator('tbody tr').first().locator('td').nth(2)).toHaveText('4');
  await page.getByText('查看日志', { exact: true }).click();
  await expect(page.locator('.logs pre')).toBeVisible();
  expect(errors).toEqual([]);
});

test('reload has no historical task cards and disables repeat updates', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '已同步，无需更新' })).toBeDisabled();
  await expect(page.locator('.task')).toHaveCount(0);
});

test('mobile has simple choices and explains unsupported ETF codes', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'ETF申赎清单', exact: true }).click();
  await page.getByLabel('ETF代码').fill('510300.SS');
  await expect(page.getByRole('alert')).toContainText('历史 PCF 目前仅支持');
  await page.getByLabel('ETF代码').fill('159919.SZ');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByLabel('自动检查结果')).toContainText('当前范围已同步');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/simtrade-ui-simple-mobile.png', fullPage: true });
});

test('write API rejects foreign origins and arbitrary commands', async ({ request }) => {
  const foreign = await request.post('/api/plans', { data: {}, headers: { Origin: 'https://example.org', 'X-SimTrade-UI': '1' } });
  expect(foreign.status()).toBe(403);
  const unsafe = await request.post('/api/plans', { data: { command: 'echo bad' }, headers: { 'X-SimTrade-UI': '1' } });
  expect(unsafe.status()).toBe(400);
});

test('live progress exposes actual byte counts and percentage', async ({ page }) => {
  await page.route('**/api/jobs', route => route.fulfill({ json: { jobs: [{
    id: 'progress-fixture', request: { dataset: 'daily', mode: 'auto', start_date: '2026-11-18', end_date: '2026-11-20' },
    status: 'running', stage: 'download', started_at: new Date().toISOString(), message: '下载沪深日线历史包',
    downloaded_bytes: 5242880, total_bytes: 10485760, bytes_per_second: 1048576,
    completed: 0, total: 1, before: [], after: null, logs: [],
  }] } }));
  await page.goto('/');
  await expect(page.locator('.live-progress')).toContainText('50.0%');
  await expect(page.locator('.progress-detail')).toContainText('5.0 MB / 10.0 MB');
  await expect(page.getByRole('progressbar')).toHaveAttribute('value', '5242880');
  await expect(page.getByRole('button', { name: '正在更新，请稍候' })).toBeDisabled();
});

test('manual date range is available only in advanced options', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByLabel('开始日期')).toHaveCount(0);
  await page.getByText('高级选项：手动日期、指定证券、强制刷新', { exact: true }).click();
  await page.getByLabel('手动指定回测范围').check();
  await page.getByLabel('开始日期').fill('2026-11-18');
  await page.getByLabel('结束日期').fill('2026-11-20');
  await expect(page.getByRole('button', { name: '已同步，无需更新' })).toBeDisabled();
  await page.getByLabel('手动指定回测范围').uncheck();
  await expect(page.getByLabel('开始日期')).toHaveCount(0);
  await expect(page.locator('.automatic-range')).toContainText('未检测到待补齐日期');
});

test('source health button checks samples and shows only latest status', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const panel = page.getByLabel('数据源健康度');
  await expect(panel.locator('details')).toHaveCount(3);
  await expect(panel).toContainText('正常');
  await panel.getByRole('button', { name: '检查数据源', exact: true }).click();
  await expect(panel.getByRole('button', { name: '检查数据源', exact: true })).toBeEnabled();
  await panel.locator('summary').first().click();
  await expect(panel).toContainText('检查时间');
  await expect(panel).toContainText('隔离测试样本有效');
  await page.screenshot({path: '/tmp/simtrade-source-health-desktop.png', fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: '/tmp/simtrade-source-health-mobile.png', fullPage: true});
  expect(errors).toEqual([]);
});

test('old running service shows restart hint instead of broken health controls', async ({ page }) => {
  await page.route('**/api/source-health', route => route.fulfill({status: 404, json: {error: 'old service'}}));
  await page.goto('/');
  const panel = page.getByLabel('数据源健康度');
  await expect(panel).toContainText('请等待更新任务完成后重启管理台');
  await expect(panel.getByRole('button', {name: '检查数据源'})).toBeDisabled();
  await expect(panel.getByRole('alert')).toHaveCount(0);
});
