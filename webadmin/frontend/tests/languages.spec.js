import { test, expect } from '@playwright/test';

const selector = 'Language / Sprache / 语言';
test('switch languages without losing ETF selection; persist on reload', async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem('simtradedata.language')) localStorage.setItem('simtradedata.language', 'zh');
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByRole('button', { name: 'ETF申赎清单', exact: true }).click();
  await page.getByText('手动输入ETF代码', { exact: true }).click();
  await page.getByLabel('ETF代码', { exact: true }).fill('159919.SZ');
  await page.getByLabel(selector).selectOption('en');
  await expect(page.getByRole('heading', { name: 'Prepare backtest data' })).toBeVisible();
  await expect(page.getByLabel('ETF codes', { exact: true })).toHaveValue('159919.SZ');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en-US');
  await page.getByLabel(selector).selectOption('de');
  await expect(page.getByRole('heading', { name: 'Backtestdaten vorbereiten', exact: true })).toBeVisible();
  await expect(page.getByLabel('ETF-Codes', { exact: true })).toHaveValue('159919.SZ');
  await expect(page.getByRole('heading', { name: 'Quartalsfinanzdaten' })).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel(selector)).toHaveValue('de');
  await expect(page).toHaveTitle('SimTradeData · Datenverwaltung');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel(selector)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/simtrade-ui-german-mobile.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('browser language determines first visit and validation errors translate', async ({ browser }) => {
  const context = await browser.newContext({ locale: 'de-DE' });
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:18765');
  await expect(page.getByLabel(selector)).toHaveValue('de');
  await expect(page).toHaveTitle('SimTradeData · Datenverwaltung');
  await page.getByLabel(selector).selectOption('en');
  await page.getByRole('button', { name: 'ETF creation/redemption lists', exact: true }).click();
  await page.getByText('Enter ETF codes manually', { exact: true }).click();
  await page.getByLabel('ETF codes', { exact: true }).fill('510300.SS');
  await expect(page.getByRole('alert')).toContainText('Historical PCF requires selected Shenzhen ETFs');
  await page.getByLabel(selector).selectOption('de');
  await expect(page.getByRole('alert')).toContainText('Historische PCF nur für ausgewählte Shenzhen-ETFs');
  await page.getByLabel(selector).selectOption('zh');
  await expect(page.getByRole('alert')).toContainText('历史 PCF');
  await context.close();
});

test('active progress and source details translate without starting a new task', async ({ page }) => {
  let writes = 0;
  await page.route('**/api/jobs', route => {
    if (route.request().method() !== 'GET') writes += 1;
    return route.fulfill({ json: { jobs: [{ id: 'language-progress', status: 'running', stage: 'write', completed: 1234, total: 2000, written: 1234, overall_completed: 1, overall_total: 4, unit_label: '季度', current_unit: '2026-09-30', message: '解析并保存', started_at: '2026-11-22T12:00:00', request: { dataset: 'fundamentals', mode: 'auto', start_date: '2026-01-01', end_date: '2026-11-20', symbols: [] }, logs: [], before: [] }] } });
  });
  await page.route('**/api/source-health', route => route.fulfill({ json: { running: false, sources: [{ key: 'fundamentals', name: '通达信季度财务', status: 'healthy', freshness: 'current', detail: '目录有效；2 个本地财务包与官方版本不同。最新已发布季度尚未缓存。' }] } }));
  await page.goto('/');
  await page.getByLabel(selector).selectOption('en');
  await expect(page.locator('.task-status')).toHaveText('Running');
  await expect(page.locator('.live-progress')).toContainText('1,234 / 2,000 companies');
  await expect(page.locator('.live-progress')).toContainText('Saved 1,234 records');
  await page.getByLabel(selector).selectOption('de');
  await expect(page.locator('.live-progress')).toContainText('1.234 / 2.000 Unternehmen');
  await page.getByText('Gespeicherte Daten und Quellen anzeigen', { exact: true }).click();
  await page.locator('.source-health summary').click();
  await expect(page.locator('.source-health')).toContainText('2 Finanzarchive weichen von offiziellen Versionen ab');
  await expect(page.locator('.source-health')).toContainText('Letztes veröffentlichtes Quartal noch nicht gespeichert');
  await page.screenshot({ path: '/tmp/simtrade-ui-german-desktop.png', fullPage: true });
  expect(writes).toBe(0);
});

test('language switching works when browser storage is disabled', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, 'localStorage', { get() { throw new Error('Storage disabled'); } }));
  await page.goto('/');
  await page.getByLabel(selector).selectOption('en');
  await expect(page.getByRole('heading', { name: 'Prepare backtest data' })).toBeVisible();
});

async function untranslatedUI(page) {
  return page.locator('main').evaluate(main => {
    const result = [];
    const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (/[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]/.test(node.textContent) &&
          !node.parentElement.closest('.security-name, .etf-full-name, .logs pre, .source-diagnostics pre') &&
          node.parentElement.checkVisibility()) result.push(node.textContent.trim());
    }
    for (const element of main.querySelectorAll('[placeholder], [aria-label], [title]')) {
      if (element.closest('.etf-row')) continue; // Accessible security names retain the official name.
      for (const attr of ['placeholder', 'aria-label', 'title']) {
        const text = element.getAttribute(attr);
        if (text && /[\u3400-\u9fff]/.test(text)) result.push(text);
      }
    }
    return result;
  });
}

for (const language of ['en', 'de']) {
  test(`${language}: real backend labels, PCF capabilities and catalogue warnings have no Chinese UI text`, async ({ page }) => {
    await page.route('**/api/overview?*', async route => {
      const response = await route.fetch();
      const data = await response.json();
      const daily = data.datasets.find(row => row.key === 'daily');
      const base = { records: 1234, securities: 1, first_date: '2026-11-18', last_date: '2026-11-20', missing_dates: [], null_fields: [] };
      daily.categories = [
        { ...base, key: 'stock_daily', name: '股票日线' },
        { ...base, key: 'etf_daily', name: 'ETF日线' },
        { ...base, key: 'other_daily', name: '其他行情（LOF/指数等）' },
      ];
      if (!data.datasets.some(row => row.key === 'pcf')) data.datasets.push({ ...base, key: 'pcf', name: 'ETF 历史 PCF', archives: [], invalid_archives: [] });
      return route.fulfill({ json: data });
    });
    await page.route('**/api/etfs', route => route.fulfill({ json: { running: false, total: 2, warnings: ['沪市ETF目录暂不可用：上交所ETF目录未完整读取'], etfs: [
      { symbol: '159919.SZ', market: 'SZ', name: '沪深300基金名称', short_name: '沪深300', history_pcf_supported: true, pcf_support: '历史可按日期查询，连续覆盖待核验' },
      { symbol: '510300.SS', market: 'SS', name: '沪市基金名称', short_name: '沪市基金', history_pcf_supported: false, pcf_support: '目前仅最新PCF，历史尚未接入', listing_info: { state: 'pre_listing', detail: '公告明确上市日为 2026-11-23' } },
    ] } }));
    await page.route('**/api/source-health', route => route.fulfill({ json: { running: false, sources: [
      { key: 'daily', name: '通达信日线', status: 'retry', detail: '日线地址返回的不是 ZIP 数据' },
      { key: 'pcf', name: '深市 PCF（159919.SZ 样本）', status: 'retry', detail: '159919.SZ: 未找到 2026-11-20 对应的 PCF 公告，不能用其他日期替代' },
    ] } }));
    await page.goto('/');
    await page.getByLabel(selector).selectOption(language);
    await expect(page.locator('.daily-stat-grid article').first()).toContainText(language === 'de' ? '1.234' : '1,234');
    await page.locator('main details').evaluateAll(details => details.forEach(detail => { detail.open = true; }));
    await expect.poll(() => untranslatedUI(page)).toEqual([]);
    await page.locator('.other-data button').last().click();
    await expect(page.locator('.etf-row')).toHaveCount(2);
    await expect(page.locator('.security-name').filter({ hasText: '沪深300' }).last()).toHaveText('沪深300');
    await page.locator('main details').evaluateAll(details => details.forEach(detail => { detail.open = true; }));
    await expect.poll(() => untranslatedUI(page)).toEqual([]);
    await page.screenshot({ path: `/tmp/simtrade-ui-${language}-pcf-translations.png`, fullPage: true });
  });

  test(`${language}: repair details and task identifiers translate; original logs remain intact`, async ({ page }) => {
    let completed = false;
    await page.route('**/api/jobs', route => route.fulfill({ json: { jobs: [{
      id: 'repair-translation', status: completed ? 'failed' : 'running', stage: 'write', message: '定向修补 600000.SS 的 1 条异常记录',
      started_at: '2026-11-22T12:00:00', completed: 0, total: 1, overall_completed: 0, overall_total: 1, unit_label: '处理单元',
      request: { dataset: 'daily', mode: 'repair', start_date: '2026-11-18', end_date: '2026-11-20', symbols: [] },
      logs: ['原始中文日志：保留核验依据'], before: [], result: {
        failed_units: [
          { unit: 'ETF目录', detail: '沪市ETF目录暂不可用；未核验新增ETF，已有历史保留。' },
          { unit: '600000.SS', detail: '1 条未修复（2026-11-18 至 2026-11-20；缺失/异常字段：money,volume）；现有来源没有匹配日期的有效数据，保留原记录待核验', missing_dates: ['2026-11-19'], source_errors: { eastmoney: 'RemoteDisconnected' } },
        ],
        lifecycle_notes: [{ symbol: '000022.SZ', date: '2018-12-25', detail: '旧股票代码退出交易：2018-12-25，源已确认，空记录不作为行情缺口' }],
      },
    }] } }));
    await page.route('**/api/source-health', route => route.fulfill({ json: { running: false, sources: [] } }));
    await page.goto('/');
    await page.getByLabel(selector).selectOption(language);
    await expect(page.locator('.task-status')).toHaveText(language === 'en' ? 'Running' : 'Läuft');
    completed = true;
    await expect(page.locator('.task-status')).toHaveText(language === 'en' ? 'Failed / partially failed' : 'Fehlgeschlagen / teilweise fehlgeschlagen');
    await page.locator('main details').evaluateAll(details => details.forEach(detail => { detail.open = true; }));
    await expect(page.locator('.logs pre')).toHaveText('原始中文日志：保留核验依据');
    await expect(page.locator('.failures')).toContainText('2026-11-19');
    await expect(page.locator('.source-diagnostics pre')).toContainText('RemoteDisconnected');
    await expect.poll(() => untranslatedUI(page)).toEqual([]);
    await page.screenshot({ path: `/tmp/simtrade-ui-${language}-repair-translations.png`, fullPage: true });
  });
}

test('catalogue errors translate from original text once when switching language', async ({ page }) => {
  await page.route('**/api/etfs', route => route.fulfill({ status: 404, json: { error: 'old service' } }));
  await page.goto('/');
  await page.getByLabel(selector).selectOption('en');
  await page.locator('.other-data button').last().click();
  await expect(page.getByRole('alert')).toContainText('ETF catalogue is not loaded');
  await page.getByLabel(selector).selectOption('de');
  await expect(page.getByRole('alert')).toContainText('ETF-Verzeichnis nicht geladen');
  await expect.poll(() => untranslatedUI(page)).toEqual([]);
});

for (const browserLocale of ['zh-CN', 'en-US']) {
  test(`first visit follows browser locale ${browserLocale}`, async ({ browser }) => {
    const context = await browser.newContext({ locale: browserLocale });
    const page = await context.newPage();
    await page.goto('http://127.0.0.1:18765');
    const chinese = browserLocale === 'zh-CN';
    await expect(page.getByLabel(selector)).toHaveValue(chinese ? 'zh' : 'en');
    await expect(page.locator('html')).toHaveAttribute('lang', chinese ? 'zh-CN' : 'en-US');
    await expect(page.getByRole('heading', { name: chinese ? '准备回测数据' : 'Prepare backtest data' })).toBeVisible();
    await context.close();
  });
}
