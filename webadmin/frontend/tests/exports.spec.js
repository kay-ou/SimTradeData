import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';

test('generate and download a real backtest package; retain it across page reloads', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('simtradedata.language', 'zh'));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const panel = page.getByRole('region', { name: '导出回测数据包' });
  await expect(panel.getByRole('button', { name: '生成数据包' })).toBeEnabled();
  await panel.getByRole('button', { name: '生成数据包' }).click();
  await expect(panel).toContainText('回测数据包已生成', { timeout: 30000 });
  await expect(panel).toContainText('ETF 3 只');
  const downloaded = page.waitForEvent('download');
  await panel.getByRole('link', { name: /下载数据包/ }).click();
  const file = await downloaded;
  expect(file.suggestedFilename()).toBe('simtradedata-cn.tar.gz');
  const data = await fs.readFile(await file.path());
  expect(data.subarray(0, 2)).toEqual(Buffer.from([0x1f, 0x8b]));
  await page.reload();
  await expect(panel.getByRole('link', { name: /下载数据包/ })).toBeVisible();
  await expect(page.locator('.export-progress')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '已是最新', exact: true })).toBeDisabled();
  await expect(page.getByLabel('已保存的股票与ETF行情')).toContainText('3 只 · 9 条日线');
  await page.screenshot({ path: '/tmp/simtrade-data-export-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/simtrade-data-export-mobile.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('export rejects arbitrary output paths and is disabled during an update', async ({ page, request }) => {
  await page.addInitScript(() => localStorage.setItem('simtradedata.language', 'en'));
  const response = await request.post('/api/exports', {
    data: { market: 'cn', output: '/tmp/unapproved' },
    headers: { 'X-SimTrade-UI': '1' },
  });
  expect(response.status()).toBe(400);
  await page.route('**/api/jobs', route => route.fulfill({ json: { jobs: [{
    id:'busy-export-test',status:'running',stage:'write',request:{dataset:'daily',mode:'auto',symbols:[],start_date:'2026-11-18',end_date:'2026-11-20'},
    started_at:'2026-11-20T12:00:00Z',completed:1,total:2,message:'处理中',logs:[],before:[],after:null,
  }] } }));
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Generate package' })).toBeDisabled();
});
