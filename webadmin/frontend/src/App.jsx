import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import Coverage from './Coverage';
import UpdateForm from './UpdateForm';
import Tasks from './Tasks';
import SourceHealth from './SourceHealth';

const iso = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const today = new Date();
const previous = new Date(today); previous.setDate(previous.getDate() - 90);
const initial = { dataset: 'daily', mode: 'auto', start_date: iso(previous), end_date: iso(today), symbols: '' };
const requestFrom = form => ({ ...form, symbols: form.symbols.split(/[,，\s]+/).filter(Boolean) });

export default function App() {
  const [form, setFormState] = useState(initial);
  const [autoRange, setAutoRange] = useState(true);
  const [recommendation, setRecommendation] = useState(null);
  const [range, setRange] = useState({ start_date: initial.start_date, end_date: initial.end_date, symbols: [] });
  const [coverage, setCoverage] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [coverageError, setCoverageError] = useState('');
  const [refresh, setRefresh] = useState(0);
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
    if (autoRange || active || !form.start_date || !form.end_date || form.start_date > form.end_date) return;
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
    const controller = new AbortController();
    setPreview(null); setRecommendation(null);
    const params = new URLSearchParams({ dataset: form.dataset, symbols: requestFrom(form).symbols.join(',') });
    api(`/api/recommendation?${params}`, undefined, controller.signal)
      .then(data => {
        setRecommendation(data); setPreview(data); setError('');
        setFormState(previous => ({ ...previous, ...data.request, symbols: previous.symbols }));
      })
      .catch(reason => { if (reason.name !== 'AbortError') setError(reason.message); });
    return () => controller.abort();
  }, [autoRange, form.dataset, form.symbols, active?.id, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const params = new URLSearchParams({ start_date: range.start_date, end_date: range.end_date, symbols: range.symbols.join(',') });
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
    setRange({ start_date: autoRange ? recommendation?.scope_start || request.start_date : request.start_date, end_date: autoRange ? recommendation?.scope_end || request.end_date : request.end_date, symbols: request.symbols });
    const details = document.querySelector('.coverage-details');
    if (details) { details.open = true; details.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  }
  async function start() {
    setPending(true); setError('');
    try {
      const plan = await api('/api/plans', requestFrom(form));
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
  async function cancel(id) {
    try { await api(`/api/jobs/${id}/cancel`, {}); setJobs((await api('/api/jobs')).jobs); }
    catch (reason) { setError(reason.message); }
  }
  function selectAutoRange(value) {
    setAutoRange(value); setPreview(null); setError('');
    if (value) setFormState(previous => ({ ...previous, mode: 'auto' }));
  }
  function retry(request) {
    setAutoRange(false);
    setForm({ ...request, symbols: request.symbols.join(', ') });
    document.getElementById('update-title')?.scrollIntoView({ behavior: 'smooth' });
  }
  function syncCalendar() {
    setAutoRange(false);
    const request = { dataset: 'calendar', mode: 'auto', start_date: range.start_date, end_date: range.end_date, symbols: [] };
    setFormState({ ...request, symbols: '' });
    document.getElementById('update-title')?.scrollIntoView({ behavior: 'smooth' });
  }
  return <>
    <header><div className="header-inner"><a className="brand" href="/">SimTradeData<span>数据管理</span></a></div></header>
    <main><div className="page-heading"><div><h1>更新你的回测数据</h1><p>选择数据，一键补齐自动检测的缺口。已有有效数据自动跳过。</p></div></div>
      <UpdateForm autoRange={autoRange} onAutoRange={selectAutoRange} recommendation={recommendation} form={form} setForm={setForm} preview={preview} onDownload={start} pending={pending} active={Boolean(active)} error={error} onCoverage={viewCoverage} />
      <SourceHealth />
      <Tasks jobs={jobs} onCancel={cancel} onRetry={retry} />
      <details className="coverage-details"><summary>本地数据详情 · 日期缺口与字段检查</summary><span className="range-label">{range.start_date} — {range.end_date}</span><Coverage data={coverage} loading={loading} error={coverageError} onRefresh={() => setRefresh(value => value + 1)} onSyncCalendar={syncCalendar} /></details>
      <footer>仅连接本机数据目录。最新快照不计入历史覆盖；未发布或不可获取的数据不会被填造。</footer>
    </main>
  </>;
}
