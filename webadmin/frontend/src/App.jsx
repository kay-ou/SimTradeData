import { t, useLanguage, locale } from './i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import Coverage from './Coverage';
import UpdateForm from './UpdateForm';
import Tasks from './Tasks';
import SourceHealth from './SourceHealth';
import RestartButton from './RestartButton';
import useEtfCatalogue from './useEtfCatalogue';
import ExportPanel from './ExportPanel';

const iso = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const today = new Date();
const previous = new Date(today); previous.setDate(previous.getDate() - 90);
const initial = { dataset: 'daily', mode: 'auto', start_date: iso(previous), end_date: iso(today), symbols: '' };
const requestFrom = form => ({ ...form, symbols: form.symbols.split(/[,，\s]+/).filter(Boolean) });

export default function App() {
  const [language, setLanguage] = useLanguage();
  const [form, setFormState] = useState(initial);
  const [autoRange, setAutoRange] = useState(true);
  const [recommendation, setRecommendation] = useState(null);
  const [range, setRange] = useState({ start_date: '1990-01-01', end_date: initial.end_date, symbols: [], include_pcf: false });
  const [coverage, setCoverage] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [coverageError, setCoverageError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const catalogue = useEtfCatalogue(refresh);
  const missingEtfs = (catalogue.data?.etfs || []).filter(item => !item.local_daily && !item.local_busy && !['pre_listing','not_established'].includes(item.listing_info?.state));
  const waitingEtfs = (catalogue.data?.etfs || []).filter(item => !item.local_daily && ['pre_listing','not_established'].includes(item.listing_info?.state));
  const active = jobs.find(job => ['running', 'cancelling'].includes(job.status));
  const previousActive = useRef(null);
  const visibleJob = useRef(null);

  useEffect(() => {
    if (previousActive.current && !active) setRefresh(value => value + 1);
    previousActive.current = active?.id || null;
  }, [active?.id]);

  const setForm = useCallback(updater => {
    setFormState(updater); setPreview(null); setError('');
  }, []);

  useEffect(() => {
    if ((form.dataset === 'pcf' && !form.symbols.trim()) || autoRange || active || !form.start_date || !form.end_date || form.start_date > form.end_date) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api('/api/plans', requestFrom(form), controller.signal)
        .then(data => { setPreview(data); setError(''); })
        .catch(reason => { if (reason.name !== 'AbortError') setError(reason.message); });
    }, 350);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [form, autoRange, active?.id, refresh]);

  useEffect(() => {
    if (!autoRange || active) return;
    if (form.dataset === 'pcf' && !form.symbols.trim()) { setPreview(null); setRecommendation(null); setError(''); return; }
    const controller = new AbortController();
    let catalogueTimer;
    setPreview(null); setRecommendation(null);
    const params = new URLSearchParams({ dataset: form.dataset, symbols: requestFrom(form).symbols.join(',') });
    api(`/api/recommendation?${params}`, undefined, controller.signal)
      .then(data => {
        setRecommendation(data); setPreview(data); setError('');
        setFormState(previous => ({ ...previous, ...data.request, symbols: previous.symbols }));
        if (data.checking_etfs) catalogueTimer = setTimeout(() => setRefresh(value => value + 1), 1500);
      })
      .catch(reason => { if (reason.name !== 'AbortError') setError(reason.message); });
    return () => { controller.abort(); clearTimeout(catalogueTimer); };
  }, [autoRange, form.dataset, form.symbols, active?.id, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const params = new URLSearchParams({ start_date: range.start_date, end_date: range.end_date, symbols: range.symbols.join(','), include_pcf: range.include_pcf ? '1' : '0' });
    api(`/api/overview?${params}`, undefined, controller.signal)
      .then(data => { setCoverage(data); setCoverageError(''); })
      .catch(reason => { if (reason.name !== 'AbortError') setCoverageError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [range, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    async function poll() {
      if (document.hidden) return;
      try {
        const data = await api('/api/jobs', undefined, controller.signal);
        if (controller.signal.aborted) return;
        const job = data.jobs[0];
        if (job && ['running', 'cancelling'].includes(job.status)) visibleJob.current = job.id;
        setJobs(job && job.id === visibleJob.current ? [job] : []);
      } catch (reason) { if (reason.name !== 'AbortError') setError(reason.message); }
    }
    poll(); const timer = setInterval(poll, 1000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);

  function viewCoverage() {
    const request = requestFrom(form);
    setRange({ start_date: autoRange ? recommendation?.scope_start || request.start_date : request.start_date, end_date: autoRange ? recommendation?.scope_end || request.end_date : request.end_date, symbols: request.symbols, include_pcf: true });
    const background = document.querySelector('.background-data');
    if (background) background.open = true;
    const details = document.querySelector('.coverage-details');
    if (details) { details.open = true; details.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  }
  async function start(override) {
    setPending(true); setError('');
    try {
      const plan = await api('/api/plans', override || requestFrom(form));
      if (plan.work_pending === false) { setPreview(plan); return; }
      const job = await api('/api/jobs', { plan_id: plan.plan_id });
      if (job.status === 'up_to_date') { setPreview({ ...plan, work_pending: false }); return; }
      visibleJob.current = job.id;
      setJobs([job]);
      setPreview(null); viewCoverage();
      document.getElementById('tasks')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (reason) { setError(reason.message); }
    finally { setPending(false); }
  }
  function repair() {
    const request = { ...requestFrom(form), mode: 'repair' };
    if (autoRange && recommendation) {
      request.start_date = recommendation.scope_start || request.start_date;
      request.end_date = recommendation.scope_end || request.end_date;
    }
    setAutoRange(false);
    setForm(previous => ({ ...previous, ...request, symbols: previous.symbols }));
    start(request);
  }
  async function cancel(id) {
    try { await api(`/api/jobs/${id}/cancel`, {}); setJobs((await api('/api/jobs')).jobs); }
    catch (reason) { setError(reason.message); }
  }
  async function startExport(market) {
    setPending(true);
    try {
      const job = await api('/api/exports', { market });
      visibleJob.current = job.id;
      setJobs([job]);
      document.getElementById('export-title')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } finally { setPending(false); }
  }
  function selectAutoRange(value) {
    setAutoRange(value); setPreview(null); setError('');
    if (value) setFormState(previous => ({ ...previous, mode: 'auto' }));
  }
  async function retry(request, resume = false, result) {
    setAutoRange(false);
    setForm({ ...request, symbols: request.symbols.join(', ') });
    if (resume) {
      const failed = result?.failed_units?.filter(row => /^\d{6}\.(SS|SZ|BJ)$/.test(row.unit)).map(row => row.unit) || [];
      if (result?.failed_units?.some(row => row.unit === 'ETF目录')) {
        await catalogue.load(true);
        setRefresh(value => value + 1);
      }
      if (request.dataset === 'daily' && !failed.length && result?.failed_units?.some(row => row.unit === 'ETF目录')) return;
      start(request.dataset === 'daily' && failed.length && failed.length <= 100 ? {...request,symbols:failed} : request);
    }
    else document.getElementById('update-title')?.scrollIntoView({ behavior: 'smooth' });
  }
  function syncCalendar() {
    setAutoRange(false);
    const request = { dataset: 'calendar', mode: 'auto', start_date: range.start_date, end_date: range.end_date, symbols: [] };
    setFormState({ ...request, symbols: '' });
    document.getElementById('update-title')?.scrollIntoView({ behavior: 'smooth' });
  }
  const storedData = <>
      <section className="daily-statistics" aria-label={t("已保存的股票与ETF行情")}><h2>{t("已保存的行情")}</h2><p>{t("统计范围：")}{range.start_date}{t(" 至 ")}{range.end_date}{coverage?.busy ? t(" · 更新中，显示已有快照") : ''}</p><div className="daily-stat-grid">{['stock_daily','etf_daily'].map(key => {
        const row = coverage?.datasets?.find(dataset => dataset.key === 'daily')?.categories?.find(category => category.key === key);
        return <article key={key}><h3>{key === 'stock_daily' ? t("股票行情") : t("ETF行情")}</h3>{row ? <><strong>{row.securities.toLocaleString(locale())}{t(" 只 · ")}{row.records.toLocaleString(locale())}{t(" 条日线")}</strong><p>{row.first_date ? t("{0} 至 {1}", [row.first_date, row.last_date]) : t("尚未保存数据")}</p></> : <p>{coverageError ? t("统计读取失败，可在本地数据详情中重试") : t("正在读取统计…")}</p>}{key === 'etf_daily' && <div className="etf-availability">
          {catalogue.error || catalogue.data?.error ? <><p>{t("ETF目录暂不可用，尚无法核查哪些ETF未下载。")}</p><button type="button" className="text-button" onClick={() => catalogue.load(true)}>{t("重新核查ETF目录")}</button></> : !catalogue.data || catalogue.data.running || catalogue.data.etfs.some(item => item.local_busy) ? <p>{t("正在核对ETF目录与本地历史…")}</p> : <>
            <p>{t("当前目录 ")}{catalogue.data.total}{t(" 只 · ")}{missingEtfs.length ? t("其中 {0} 只未获取历史", [missingEtfs.length]) : waitingEtfs.length ? t("已上市证券均有本地历史记录") : t("每只均有本地历史记录")}</p>
            {!!missingEtfs.length && <details className="etf-missing"><summary>{t("查看未获取历史的ETF（{0}）", [missingEtfs.length])}</summary><ul>{missingEtfs.map(item => <li key={item.symbol}><strong>{item.symbol}</strong> <span className="security-name">{item.short_name || item.name}</span><span className="cell-note">{t("本地暂无历史，仍需获取或核验上市状态")}</span></li>)}</ul></details>}
            {!!waitingEtfs.length && <details className="etf-missing"><summary>{t("尚未进入上市交易阶段的ETF（{0}）", [waitingEtfs.length])}</summary><ul>{waitingEtfs.map(item => <li key={item.symbol}><strong>{item.symbol}</strong> <span className="security-name">{item.short_name || item.name}</span><span className="cell-note">{t(item.listing_info.detail)}</span></li>)}</ul></details>}
            <p className="field-help">{catalogue.data.warnings?.length ? t("目录来源不完整，名单仍需核验。") : t("按当前目录核查；已有记录不代表上市以来历史完整。")}</p>
          </>}
        </div>}</article>;
      })}</div></section>
      <SourceHealth />
      <details className="coverage-details"><summary>{t("本地数据详情 · 日期缺口与字段检查")}</summary><span className="range-label">{range.start_date} — {range.end_date}</span><Coverage data={coverage} loading={loading} error={coverageError} onRefresh={() => setRefresh(value => value + 1)} onSyncCalendar={syncCalendar} /></details>
  </>;
  return <>
    <header><div className="header-inner"><a className="brand" href="/">SimTradeData<span>{t("数据管理")}</span></a><select className="language-select" aria-label="Language / Sprache / 语言" value={language} onChange={event => setLanguage(event.target.value)}><option value="zh">中文</option><option value="en">English</option><option value="de">Deutsch</option></select></div></header>
    <main><div className="page-heading"><div><h1>{t("准备回测数据")}</h1><p>{t("股票与ETF一起更新，已有有效数据自动跳过。")}{t("更新完成后，生成并下载下方的数据包。")}</p></div><RestartButton active={Boolean(active) || pending} /></div>
      {!active && <UpdateForm catalogue={catalogue} lastJob={jobs[0]} onRecheck={() => setRefresh(value => value + 1)} onTaskDetails={() => { const details = document.querySelector('#tasks .failures'); if (details) { details.open = true; details.scrollIntoView({ behavior: 'smooth', block: 'center' }); } }} autoRange={autoRange} onAutoRange={selectAutoRange} recommendation={recommendation} form={form} setForm={setForm} preview={preview} onDownload={start} pending={pending} active={Boolean(active)} error={error} onCoverage={viewCoverage} onRepair={repair} />}
      <Tasks jobs={jobs.filter(job => job.request.dataset !== 'export')} onCancel={cancel} onRetry={retry} />
      <ExportPanel active={active} pending={pending} refresh={refresh} job={jobs.find(job => job.request.dataset === 'export')} onExport={startExport} onCancel={cancel} />
      {active ? <details className="background-data"><summary>{t("查看已有数据与数据源")}</summary>{storedData}</details> : storedData}
      <footer>{t("仅连接本机数据目录。最新快照不计入历史覆盖；未发布或不可获取的数据不会被填造。")}</footer>
    </main>
  </>;
}
