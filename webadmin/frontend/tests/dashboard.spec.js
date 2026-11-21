import { test, expect } from '@playwright/test';

test('coverage → preview → real cached import → verification', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page).toHaveTitle('SimTradeData · 数据管理');
  await expect(page.getByRole('heading', { name: '让历史数据保持完整' })).toBeVisible();
  await page.getByLabel('开始日期').fill('2026-11-18');
  await page.getByLabel('结束日期').fill('2026-11-20');
  await page.getByRole('button', { name: '查看此范围覆盖' }).click();
  await expect(page.locator('.range-label')).toHaveText('2026-11-18 — 2026-11-20');
  await expect(page.locator('tbody tr').first()).toContainText('2 个缺口');
  await page.screenshot({ path: '/tmp/simtrade-ui-desktop.png', fullPage: true });
  await page.getByLabel('更新方式').selectOption('reparse');
  await page.getByLabel('证券代码').fill('600000.SS');
  await page.getByRole('button', { name: '生成更新预览' }).click();
  await expect(page.getByLabel('更新预览')).toContainText('1 个处理单元');
  await page.getByRole('button', { name: '开始更新', exact: true }).click();
  await expect(page.locator('.task-status')).toHaveText('已完成', { timeout: 30000 });
  await expect(page.locator('.comparison')).toContainText('1 → 3');
  await expect(page.locator('.comparison')).toContainText('2 → 0');
  await expect(page.locator('tbody tr').first().locator('td').nth(2)).toHaveText('3');
  await expect(page.getByText('任务更新中，保留开始前的覆盖快照；完成后重新检查。')).toHaveCount(0);
  await page.getByText('查看日志', { exact: true }).click();
  await expect(page.locator('.logs pre')).toBeVisible();
  await page.getByRole('button', { name: '使用此任务参数' }).click();
  await expect(page.getByLabel('证券代码')).toHaveValue('600000.SS');
  await page.screenshot({ path: '/tmp/simtrade-ui-completed.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('invalid request is explained; mobile controls remain usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByLabel('数据类别').selectOption('pcf');
  await page.getByLabel('开始日期').fill('2026-11-18');
  await page.getByLabel('结束日期').fill('2026-11-20');
  await page.getByLabel('证券代码').fill('510300.SS');
  await page.getByRole('button', { name: '生成更新预览' }).click();
  await expect(page.getByRole('alert')).toContainText('历史 PCF 目前仅支持');
  await expect(page.getByRole('button', { name: '开始更新', exact: true })).toHaveCount(0);
  await page.getByLabel('证券代码').fill('159919.SZ');
  await page.getByRole('button', { name: '生成更新预览' }).click();
  await expect(page.getByLabel('更新预览')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/simtrade-ui-mobile.png', fullPage: true });
});

test('write API rejects foreign origins and arbitrary commands', async ({ request }) => {
  const foreign = await request.post('/api/plans', { data: {}, headers: { Origin: 'https://example.org', 'X-SimTrade-UI': '1' } });
  expect(foreign.status()).toBe(403);
  const unsafe = await request.post('/api/plans', { data: { command: 'echo bad' }, headers: { 'X-SimTrade-UI': '1' } });
  expect(unsafe.status()).toBe(400);
});

test('download button is visible and live progress renders byte counts', async ({ page }) => {
  await page.route('**/api/jobs', route => route.request().method() === 'GET' ? route.fulfill({ json: { jobs: [] } }) : route.continue());
  await page.goto('/');
  await expect(page.getByRole('button', { name: '开始下载', exact: true })).toBeVisible();
  await page.unroute('**/api/jobs');
  const startedAt = new Date().toISOString();
  await page.route('**/api/jobs', route => route.fulfill({ json: { jobs: [{
    id: 'progress-fixture', request: { dataset: 'daily', mode: 'incremental', start_date: '2026-11-18', end_date: '2026-11-20' },
    status: 'running', stage: 'download', started_at: startedAt, message: '下载沪深日线历史包',
    downloaded_bytes: 5242880, total_bytes: 10485760, bytes_per_second: 1048576,
    completed: 0, total: 1, before: [], after: null, logs: [],
  }] } }));
  await expect(page.locator('.live-progress')).toContainText('50.0%');
  await expect(page.locator('.progress-detail')).toContainText('5.0 MB / 10.0 MB');
  await expect(page.getByRole('progressbar')).toHaveAttribute('value', '5242880');
  await page.screenshot({ path: '/tmp/simtrade-ui-live-progress.png', fullPage: true });
});
