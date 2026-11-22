import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('simtradedata.language', 'zh'));
});

for (const symbols of ['159919.SZ, 159920.SZ', '159919.SZ，159920.SZ', '159919.SZ 159920.SZ']) {
  test(`automatic recommendations normalize symbols: ${symbols}`, async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'ETF申赎清单', exact: true }).click();
    const responsePromise = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/recommendation' && url.searchParams.get('symbols') === '159919.SZ,159920.SZ';
    });
    await page.getByText('手动输入ETF代码', {exact: true}).click();
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
  await expect(page.getByLabel('自动检查结果')).toContainText('待补齐');
  await expect(page.locator('.automatic-range')).toContainText('2026-11-18 至 2026-11-20');
  await expect(page.getByLabel('开始日期')).toHaveCount(0);
  await page.screenshot({ path: '/tmp/simtrade-ui-simple-desktop.png', fullPage: true });
  await page.getByRole('button', { name: '更新回测数据', exact: true }).click();
  await expect(page.locator('.task-status')).toHaveText('已完成', { timeout: 30000 });
  await expect(page.locator('.comparison')).toContainText('1 → 3');
  await expect(page.locator('.comparison')).toContainText('0 → 9');
  await expect(page.getByRole('button', { name: '已是最新' })).toBeDisabled();
  await expect(page.getByRole('heading', { name: '更新记录' })).toHaveCount(0);
  await page.getByRole('button', { name: '查看本地数据详情' }).click();
  await expect(page.locator('tbody tr').first().locator('td').nth(2)).toHaveText('4');
  await page.getByText('查看日志', { exact: true }).click();
  await expect(page.locator('.logs pre')).toBeVisible();
  expect(errors).toEqual([]);
});

test('reload has no historical task cards and disables repeat updates', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '已是最新' })).toBeDisabled();
  await expect(page.locator('.task')).toHaveCount(0);
});

test('mobile has simple choices and explains unsupported ETF codes', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'ETF申赎清单', exact: true }).click();
  await page.getByText('手动输入ETF代码', {exact: true}).click();
  await page.getByLabel('ETF代码').fill('510300.SS');
  await expect(page.getByRole('alert')).toContainText('历史 PCF 目前仅支持');
  await page.getByLabel('ETF代码').fill('159919.SZ');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByLabel('自动检查结果')).toContainText('已是最新');
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
  await expect(page.locator('.primary-download')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'取消任务',exact:true})).toBeEnabled();
});

test('manual date range is available only in advanced options', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByLabel('开始日期')).toHaveCount(0);
  await page.getByText('高级选项：手动日期、指定证券、强制刷新', { exact: true }).click();
  await page.getByLabel('手动指定回测范围').check();
  await page.getByLabel('开始日期').fill('2026-11-18');
  await page.getByLabel('结束日期').fill('2026-11-20');
  await expect(page.getByRole('button', { name: '已是最新' })).toBeDisabled();
  await page.getByLabel('手动指定回测范围').uncheck();
  await expect(page.getByLabel('开始日期')).toHaveCount(0);
  await expect(page.getByLabel('自动检查结果')).toContainText('已是最新');
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

test('restart button is disabled while an update is running', async ({ page }) => {
  await page.route('**/api/jobs', route => route.fulfill({json: {jobs: [{id: 'running-fixture', request: {dataset: 'daily', mode: 'auto', start_date: '2026-11-18', end_date: '2026-11-20'}, status: 'running', stage: 'write', started_at: new Date().toISOString(), completed: 0, total: 1, message: '正在写入', logs: [], before: []}]}}));
  await page.goto('/');
  await expect(page.getByRole('button', {name: '重启管理台'})).toBeDisabled();
});

test('restart launches a new server instance and preserves downloaded rows', async ({ page, request }) => {
  const previous = await (await request.get('/api/service')).json();
  await page.goto('/');
  await expect(page.getByRole('button', {name: '重启管理台'})).toBeEnabled();
  await page.getByRole('button', {name: '重启管理台'}).click();
  await expect.poll(async () => {
    try { return (await (await request.get('/api/service')).json()).instance_id; }
    catch { return previous.instance_id; }
  }, {timeout: 30000}).not.toBe(previous.instance_id);
  await expect(page.getByRole('button', {name: '重启管理台'})).toBeEnabled();
  await expect(page.getByRole('button', {name: '已是最新'})).toBeDisabled();
  await expect(page.locator('.task')).toHaveCount(0);
  await page.screenshot({path: '/tmp/simtrade-restart-desktop.png', fullPage: true});
});

test('financial summary explains missing quarters and planned downloads', async ({ page }) => {
  await page.route('**/api/recommendation?*', async route => {
    if (!route.request().url().includes('dataset=fundamentals')) return route.continue();
    await route.fulfill({json: {request: {dataset: 'fundamentals', mode: 'auto', start_date: '2026-03-31', end_date: '2026-09-30', symbols: []}, work_pending: true, cached_files: 2, existing_records: 0, update_summary: {pending_units: 3, reuse_units: 2, download_units: 1}, initialized: true}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: '季度财务', exact: true}).click();
  const summary = page.getByLabel('自动检查结果');
  await expect(summary).toContainText('3 个季度待补齐');
  await expect(summary).toContainText('优先复用 2 个缓存包 · 需要下载 1 个季度包');
  await expect(page.locator('.automatic-range')).toContainText('2026-03-31 至 2026-09-30');
  await page.screenshot({path: '/tmp/simtrade-update-summary.png', fullPage: true});
});

test('current quarter reaching 100 percent does not mean the task is complete', async ({ page }) => {
  const job = {id: 'overall-fixture', request: {dataset: 'fundamentals', mode: 'auto', start_date: '2026-03-31', end_date: '2026-09-30'}, status: 'running', stage: 'write', started_at: new Date().toISOString(), completed: 5500, total: 5500, current_unit: '2026-09-30', overall_completed: 2, overall_total: 3, unit_label: '季度', message: '正在提交当前季度', logs: [], before: []};
  await page.route('**/api/jobs', route => route.fulfill({json: {jobs: [job]}}));
  await page.goto('/');
  await expect(page.locator('.overall-progress')).toContainText('2 / 3 季度');
  await expect(page.locator('.live-progress')).toContainText('当前季度 2026-09-30');
  await expect(page.locator('.live-progress')).toContainText('100.0%');
  await expect(page.locator('.task-status')).toHaveText('运行中');
  await page.screenshot({path: '/tmp/simtrade-overall-progress.png', fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: '/tmp/simtrade-overall-progress-mobile.png', fullPage: true});
  job.stage = 'verify'; job.overall_completed = 3; job.current_unit = null;
  await expect(page.locator('.overall-progress')).toContainText('任务尚未完成');
  await expect(page.getByRole('progressbar', {name: '整体处理进度'})).not.toHaveAttribute('value');
  await expect(page.locator('.live-progress')).toHaveCount(0);
  await expect(page.locator('.task-status')).toHaveText('运行中');
});

test('field warnings remain visible without offering repeated daily download', async ({ page }) => {
  await page.route('**/api/recommendation?*', route => route.fulfill({json: {request: {dataset: 'daily', mode: 'auto', start_date: '2015-01-05', end_date: '2026-11-20', symbols: []}, work_pending: false, existing_records: 100, cached_files: 1, initialized: true, update_summary: {quality_issues: 2}}}));
  await page.goto('/');
  await expect(page.getByLabel('自动检查结果')).toContainText('日期已同步；2 条记录需核验');
  await expect(page.getByRole('button', {name: '已是最新'})).toBeDisabled();
  await expect(page.getByText('智能更新不会因此重复下载整包', {exact: false})).toBeVisible();
});

test('repair button starts a targeted task and reports unresolved records', async ({ page }) => {
  let job = null;
  await page.route('**/api/recommendation?*', route => route.fulfill({json: {request: {dataset: 'daily', mode: 'auto', start_date: '2026-11-18', end_date: '2026-11-20', symbols: []}, scope_start: '2026-11-17', scope_end: '2026-11-20', work_pending: true, existing_records: 3, cached_files: 1, initialized: true, update_summary: {quality_issues: 2}}}));
  await page.route('**/api/plans', route => {
    expect(route.request().postDataJSON()).toEqual({dataset: 'daily', mode: 'repair', start_date: '2026-11-17', end_date: '2026-11-20', symbols: []});
    return route.fulfill({json: {plan_id: 'repair-preview', work_pending: true, update_summary: {quality_issues: 2}}});
  });
  await page.route('**/api/jobs', route => {
    if (route.request().method() === 'POST') {
      expect(route.request().postDataJSON()).toEqual({plan_id: 'repair-preview'});
      job = {id: 'repair-fixture', request: {dataset: 'daily', mode: 'repair', start_date: '2026-11-18', end_date: '2026-11-20', symbols: []}, status: 'running', stage: 'write', started_at: new Date().toISOString(), completed: 0, total: 1, overall_completed: 0, overall_total: 1, unit_label: '个证券', message: '定向修补异常记录', logs: [], before: []};
      return route.fulfill({json: job});
    }
    return route.fulfill({json: {jobs: job ? [job] : []}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: '修补不完整数据'}).click();
  await expect(page.locator('.task-heading')).toContainText('定向修补');
  job = {...job, status: 'failed', stage: 'verify', after: [], result: {written: 1, unresolved: 1, failed_units: [{unit: '000594.SZ', detail: '1 条未修复；源没有对应历史日期'}]}};
  await expect(page.locator('.comparison')).toContainText('已修复 1 条');
  await expect(page.locator('.comparison')).toContainText('仍待核验 1 条');
  await page.locator('.failures summary').click();
  await expect(page.locator('.failures')).toContainText('源没有对应历史日期');
  await page.screenshot({path: '/tmp/simtrade-targeted-repair.png', fullPage: true});
});

test('closed security placeholders are explained separately from missing market data', async ({ page }) => {
  await page.route('**/api/recommendation?*', route => route.fulfill({json: {request: {dataset: 'daily', mode: 'auto', start_date: '2015-01-05', end_date: '2026-11-20', symbols: []}, work_pending: false, existing_records: 100, cached_files: 1, initialized: true, update_summary: {quality_issues: 0, not_applicable_records: 6}}}));
  await page.goto('/');
  await expect(page.getByText('6 条记录位于证券退出日及以后', {exact: false})).toBeVisible();
  await expect(page.getByRole('button', {name: '修补不完整数据'})).toHaveCount(0);
  await expect(page.getByRole('button', {name: '已是最新'})).toBeDisabled();
});

test('ETF catalogue searches and selects multiple Shenzhen history funds without default sample', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', {name: 'ETF申赎清单', exact: true}).click();
  await expect(page.getByRole('button', {name: '请先选择ETF'})).toBeDisabled();
  const picker = page.getByLabel('ETF选择器');
  await expect(picker).toContainText('目录 3 只');
  await expect(picker.getByRole('checkbox', {name: /159919.SZ/})).not.toBeChecked();
  await expect(picker.getByRole('checkbox', {name: /510300.SS/})).toBeDisabled();
  await picker.getByLabel('搜索ETF').fill('恒生');
  await expect(picker.locator('.etf-row')).toHaveCount(1);
  await picker.getByRole('checkbox', {name: /159920.SZ/}).check();
  await picker.getByLabel('搜索ETF').fill('嘉实');
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/recommendation' && new URL(r.url()).searchParams.get('symbols') === '159920.SZ,159919.SZ');
  await picker.getByRole('checkbox', {name: /159919.SZ/}).check();
  expect((await (await response).json()).request.symbols).toEqual(['159920.SZ','159919.SZ']);
  await expect(picker).toContainText('已选 2 / 100');
  await picker.getByLabel('搜索ETF').fill('');
  await page.screenshot({path:'/tmp/simtrade-etf-selector-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/simtrade-etf-selector-mobile.png',fullPage:true});
});

test('Shanghai ETF is selectable for normal price backtests', async ({ page }) => {
  await page.goto('/');
  await page.getByText('高级选项：手动日期、指定证券、强制刷新', {exact:true}).click();
  await page.getByText('只更新指定ETF（可选；默认自动更新全部股票与ETF）', {exact:true}).click();
  const picker = page.getByLabel('ETF选择器');
  await expect(picker).toContainText('目录 3 只');
  await picker.getByLabel('搜索ETF').fill('510300');
  const checkbox = picker.getByRole('checkbox', {name:/510300.SS/});
  await expect(checkbox).toBeEnabled();
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/recommendation' && new URL(r.url()).searchParams.get('symbols') === '510300.SS');
  await checkbox.check();
  const result = await (await response).json();
  expect(result.request.dataset).toBe('daily');
  expect(result.request.symbols).toEqual(['510300.SS']);
});

test('catalogue failure is visible and does not silently select 159919', async ({ page }) => {
  await page.route('**/api/etfs', route => route.fulfill({json:{running:false,total:0,etfs:[],error:'目录暂不可用',warnings:[]}}));
  await page.goto('/');
  await page.getByRole('button', {name:'ETF申赎清单',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('目录暂不可用');
  await expect(page.getByRole('button', {name:'请先选择ETF'})).toBeDisabled();
  await expect(page.getByLabel('ETF选择器').getByRole('checkbox')).toHaveCount(0);
});

test('default daily flow clearly updates stocks and ETFs together', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const ready = {request: {dataset:'daily',mode:'auto',start_date:'2015-01-01',end_date:'2026-11-20',symbols:[]},work_pending:true,existing_records:100,cached_files:1,initialized:true,scope_start:'2015-01-01',scope_end:'2026-11-20',update_summary:{quality_issues:0}};
  await page.route('**/api/recommendation?*', route => route.fulfill({json:ready}));
  let posted;
  await page.route('**/api/plans', async route => {
    posted = route.request().postDataJSON();
    await route.fulfill({json:{...ready,plan_id:'unified-daily'}});
  });
  let started = false;
  await page.route('**/api/jobs', async route => {
    if (route.request().method() === 'POST') {
      expect(route.request().postDataJSON()).toEqual({plan_id:'unified-daily'});
      started = true;
      return route.fulfill({json:{id:'unified-job',status:'running',request:ready.request,stage:'download',message:'正在更新股票和ETF',logs:[],before:[],completed:0,total:1}});
    }
    return route.fulfill({json:{jobs:started ? [{id:'unified-job',status:'running',request:ready.request,stage:'download',message:'正在更新股票和ETF',logs:[],before:[],completed:0,total:1}] : []}});
  });
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'股票与ETF行情',exact:true})).toBeVisible();
  await expect(page.getByText('更新范围：全部股票和ETF',{exact:true})).toBeVisible();
  await expect(page.getByLabel('搜索ETF')).not.toBeVisible();
  await expect(page.getByLabel('开始日期')).not.toBeVisible();
  await expect(page.getByLabel('更新方式')).not.toBeVisible();
  const download = page.getByRole('button',{name:'更新回测数据',exact:true});
  await expect(download).toBeEnabled();
  await page.screenshot({path:'/tmp/simtrade-unified-daily-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/simtrade-unified-daily-mobile.png',fullPage:true});
  await download.click();
  expect(posted).toEqual(ready.request);
  await expect(page.locator('.task-status')).toHaveText('运行中');
  expect(errors).toEqual([]);
});

test('advanced security filter is visible in scope and can restore all', async ({ page }) => {
  await page.route('**/api/recommendation?*', route => {
    const symbols = new URL(route.request().url()).searchParams.get('symbols').split(',').filter(Boolean);
    return route.fulfill({json:{request:{dataset:'daily',mode:'auto',start_date:'2015-01-01',end_date:'2026-11-20',symbols},work_pending:true,existing_records:0,cached_files:1,scope_start:'2015-01-01',scope_end:'2026-11-20'}});
  });
  await page.goto('/');
  await page.getByText('高级选项：手动日期、指定证券、强制刷新',{exact:true}).click();
  await page.getByLabel('证券代码',{exact:true}).fill('600000.SS,510300.SS');
  await expect(page.getByText('仅更新 2 只所选证券',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'更新所选证券',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'恢复更新全部',exact:true}).click();
  await expect(page.getByLabel('证券代码',{exact:true})).toHaveValue('');
  await expect(page.getByRole('button',{name:'更新回测数据',exact:true})).toBeEnabled();
});

test('catalogue loading finishes by itself and then shows synchronized', async ({ page }) => {
  let requests = 0;
  await page.route('**/api/recommendation?*', route => {
    requests += 1;
    return route.fulfill({json:{request:{dataset:'daily',mode:'auto',start_date:'2015-01-01',end_date:'2026-11-20',symbols:[]},checking_etfs:requests === 1,work_pending:requests === 1,existing_records:100,cached_files:1,scope_start:'2015-01-01',scope_end:'2026-11-20'}});
  });
  await page.goto('/');
  await expect(page.getByRole('button',{name:'正在检查本地数据…',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'已是最新',exact:true})).toBeDisabled({timeout:10000});
  expect(requests).toBeGreaterThanOrEqual(2);
});

test('stocks and ETFs have separate visible saved-data statistics', async ({page}) => {
  await page.goto('/');
  const statistics = page.getByLabel('已保存的股票与ETF行情');
  await expect(statistics.getByRole('heading',{name:'股票行情',exact:true})).toBeVisible();
  await expect(statistics.getByRole('heading',{name:'ETF行情',exact:true})).toBeVisible();
  await expect(statistics.locator('article').first()).toContainText('1 只 · 4 条日线');
  await expect(statistics.locator('article').nth(1)).toContainText('3 只 · 9 条日线');
  await page.getByRole('button',{name:'查看本地数据详情',exact:true}).click();
  const table = page.locator('tbody');
  await expect(table.getByText('股票日线',{exact:true})).toBeVisible();
  await expect(table.getByText('ETF日线',{exact:true})).toBeVisible();
  await page.screenshot({path:'/tmp/simtrade-separate-etf-statistics-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/simtrade-separate-etf-statistics-mobile.png',fullPage:true});
});

test('ETF and manual-date checkboxes stay compact and aligned on desktop and mobile', async ({page}) => {
  const errors = [];
  page.on('pageerror',error => errors.push(error.message));
  await page.goto('/');
  await page.getByText('高级选项：手动日期、指定证券、强制刷新',{exact:true}).click();
  await page.getByText('只更新指定ETF（可选；默认自动更新全部股票与ETF）',{exact:true}).click();
  const picker = page.getByLabel('ETF选择器');
  await expect(picker).toContainText('目录 3 只');
  const etf = picker.getByRole('checkbox',{name:/510300.SS/});
  const manual = page.getByLabel('手动指定回测范围',{exact:true});
  for (const width of [1440,390]) {
    await page.setViewportSize({width,height:1000});
    for (const checkbox of [etf,manual]) {
      const box = await checkbox.boundingBox();
      expect(box.width).toBeLessThanOrEqual(18);
      expect(box.height).toBeLessThanOrEqual(18);
    }
    const alignment = await etf.evaluate(input => {
      const checkbox = input.getBoundingClientRect();
      const text = input.nextElementSibling.getBoundingClientRect();
      return {gap:text.x-checkbox.right,top:Math.abs(text.y-checkbox.y)};
    });
    expect(alignment.gap).toBeGreaterThanOrEqual(8);
    expect(alignment.gap).toBeLessThanOrEqual(16);
    expect(alignment.top).toBeLessThanOrEqual(5);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await etf.check();
  await expect(page.getByText('仅更新 1 只所选证券',{exact:true})).toBeVisible();
  await manual.focus();
  await page.keyboard.press('Space');
  await expect(manual).toBeChecked();
  await expect(page.getByLabel('开始日期')).toBeVisible();
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:'/tmp/simtrade-compact-checkbox-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'/tmp/simtrade-compact-checkbox-mobile.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('ETF coverage shows the missing-history list without claiming full completeness', async ({page}) => {
  await page.route('**/api/overview?*',async route => {
    const response=await route.fetch(),data=await response.json();
    const daily=data.datasets.find(row => row.key==='daily');
    const etf=daily.categories.find(row => row.key==='etf_daily');
    Object.assign(etf,{records:3,securities:1,first_date:'2026-11-18',last_date:'2026-11-20',dates:['2026-11-18','2026-11-19','2026-11-20'],missing_dates:[]});
    daily.records=daily.categories.reduce((sum,row)=>sum+row.records,0);
    daily.securities=daily.categories.reduce((sum,row)=>sum+row.securities,0);
    return route.fulfill({json:data});
  });

  await page.route('**/api/recommendation?*',route => route.fulfill({json:{request:{dataset:'daily',mode:'auto',start_date:'2015-01-01',end_date:'2026-11-20',symbols:[]},work_pending:true,existing_records:100,cached_files:1,scope_start:'2015-01-01',scope_end:'2026-11-20'}}));

  await page.route('**/api/etfs',route => route.fulfill({json:{running:false,total:3,warnings:[],error:null,etfs:[
    {symbol:'159919.SZ',name:'已下载ETF',short_name:'已下载ETF',local_daily:{records:3,first_date:'2026-11-18',last_date:'2026-11-20'}},
    {symbol:'159920.SZ',name:'待下载ETF一',short_name:'待下载ETF一',local_daily:null},
    {symbol:'510300.SS',name:'待下载ETF二',short_name:'待下载ETF二',local_daily:null},
  ]}}));
  await page.goto('/');
  const statistics=page.getByLabel('已保存的股票与ETF行情');
  await expect(statistics).toContainText('当前目录 3 只 · 其中 2 只未获取历史');
  await statistics.getByText('查看未获取历史的ETF（2）',{exact:true}).click();
  await expect(statistics.locator('.etf-missing li')).toHaveCount(2);
  await expect(statistics.locator('.etf-missing')).toContainText('159920.SZ');
  await expect(statistics.locator('.etf-missing')).toContainText('510300.SS');
  await expect(statistics.locator('.etf-missing')).not.toContainText('159919.SZ');
  await expect(statistics).toContainText('已有记录不代表上市以来历史完整');
  await expect(page.locator('.primary-download')).toHaveCount(1);
  await expect(page.getByRole('button',{name:'更新回测数据',exact:true})).toBeEnabled();
  await expect(statistics.locator('article').first()).toContainText('条日线');
  await expect(statistics.locator('article').nth(1)).toContainText('1 只 · 3 条日线');
  await expect(page.locator('.other-data')).toContainText('季度财务');
  await expect(page.locator('.other-data')).toContainText('ETF申赎清单');
  await page.screenshot({path:'/tmp/simtrade-friendly-overview-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/simtrade-friendly-overview-mobile.png',fullPage:true});
});

test('running updates focus progress and partial failure offers a real resume action', async ({page}) => {
  const request={dataset:'daily',mode:'auto',start_date:'2015-01-01',end_date:'2026-11-20',symbols:[]};
  let job={id:'focus-task',request,status:'running',stage:'download',started_at:new Date().toISOString(),message:'正在保存ETF日线',completed:5,total:10,written:7,logs:[],before:[],after:null};
  await page.route('**/api/recommendation?*',route => route.fulfill({json:{request,work_pending:true,existing_records:100,cached_files:1,scope_start:'2015-01-01',scope_end:'2026-11-20'}}));
  let resumed;
  await page.route('**/api/plans',route => {
    resumed=route.request().postDataJSON();
    return route.fulfill({json:{request,work_pending:true,plan_id:'resume-failed',existing_records:100,cached_files:1}});
  });
  await page.route('**/api/jobs',route => {
    if (route.request().method()==='POST') {
      job={...job,id:'resumed-task',status:'running',stage:'download',result:undefined};
      return route.fulfill({json:job});
    }
    return route.fulfill({json:{jobs:[job]}});
  });
  await page.goto('/');
  await expect(page.locator('.task-status')).toHaveText('运行中');
  await expect(page.locator('.live-progress').getByText('正在保存ETF日线',{exact:false})).toBeVisible();
  await expect(page.getByText('已保存 7 条',{exact:true})).toBeVisible();
  await expect(page.locator('.background-data')).not.toHaveAttribute('open','');
  await expect(page.getByLabel('已保存的股票与ETF行情')).not.toBeVisible();
  expect((await page.locator('#tasks').boundingBox()).y).toBeLessThan(350);
  await page.screenshot({path:'/tmp/simtrade-friendly-running.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('.live-progress')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/simtrade-friendly-running-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  job={...job,status:'failed',stage:'verify',result:{written:10,failed_units:[{unit:'159920.SZ',detail:'历史源未返回数据'}]}};
  await expect(page.getByLabel('自动检查结果')).toContainText('部分数据未获取');
  await page.getByRole('button',{name:'查看未完成名单',exact:true}).click();
  await expect(page.locator('#tasks .failures')).toHaveAttribute('open','');
  await expect(page.locator('#tasks .failures')).toContainText('159920.SZ');
  await page.getByRole('button',{name:'继续补齐',exact:true}).click();
  await expect(page.locator('.task-status')).toHaveText('运行中');
  expect(resumed).toEqual({...request,symbols:['159920.SZ']});
});

for (const [dataset, unit] of [['financial', '2026-09-30'], ['pcf', '159919.SZ|2026-11-20']]) {
  test(`failed ${dataset} tasks resume their original request`, async ({page}) => {
    const request={dataset,mode:'auto',start_date:'2026-01-01',end_date:'2026-11-20',symbols:dataset==='pcf'?['159919.SZ']:[]};
    let job={id:`failed-${dataset}`,request,status:'running',stage:'download',started_at:new Date().toISOString(),logs:[],before:[],result:null};
    let resumed;
    await page.route('**/api/plans',route => {
      resumed=route.request().postDataJSON();
      return route.fulfill({json:{work_pending:true,plan_id:'resume-failed'}});
    });
    await page.route('**/api/jobs',route => {
      if (route.request().method()==='POST') {
        job={...job,id:`resumed-${dataset}`,status:'running',result:null};
        return route.fulfill({json:job});
      }
      return route.fulfill({json:{jobs:[job]}});
    });
    await page.goto('/');
    await expect(page.locator('.task-status')).toHaveText('运行中');
    job={...job,status:'failed',result:{failed_units:[{unit,detail:'数据源暂不可用'}]}};
    await page.getByRole('button',{name:'继续补齐',exact:true}).click();
    await expect(page.locator('.task-status')).toHaveText('运行中');
    expect(resumed).toEqual(request);
  });
}

test('rechecking a read error does not start a download', async ({page}) => {
  let checks=0,plans=0;
  await page.route('**/api/recommendation?*',route => {
    checks+=1;
    return checks===1 ? route.fulfill({status:503,json:{error:'本地检查暂不可用'}}) : route.fulfill({json:{request:{dataset:'daily',mode:'auto',start_date:'2015-01-01',end_date:'2026-11-20',symbols:[]},work_pending:false,existing_records:100,cached_files:1}});
  });
  await page.route('**/api/plans',route => {plans+=1;return route.fulfill({json:{work_pending:false}});});
  await page.goto('/');
  await expect(page.getByLabel('自动检查结果')).toContainText('暂时无法检查数据');
  await page.getByRole('button',{name:'重新检查',exact:true}).click();
  await expect(page.getByRole('button',{name:'已是最新',exact:true})).toBeDisabled();
  expect(checks).toBeGreaterThanOrEqual(2);
  expect(plans).toBe(0);
});

test('daily auto plan accepts omitted symbols with the ETF catalogue enabled', async ({ request }) => {
  const response = await request.post('/api/plans', {
    headers: { 'X-SimTrade-UI': '1' },
    data: { dataset: 'daily', mode: 'auto', start_date: '2026-11-18', end_date: '2026-11-20' },
  });
  expect(response.ok()).toBe(true);
  expect((await response.json()).request.symbols).toEqual([]);
});
