import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import Coverage from './Coverage';
import UpdateForm from './UpdateForm';
import Tasks from './Tasks';

const iso = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const today = new Date();
const previous = new Date(today); previous.setDate(previous.getDate() - 90);
const initial = { dataset: 'daily', mode: 'incremental', start_date: iso(previous), end_date: iso(today), symbols: '' };
const requestFrom = form => ({ ...form, symbols: form.symbols.split(/[,，\s]+/).filter(Boolean) });

export default function App() {
  const [form, setFormState] = useState(initial);
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

  useEffect(() => {
    if (previousActive.current && !active) setRefresh(value => value + 1);
    previousActive.current = active?.id || null;
  }, [active?.id]);

  const setForm = useCallback(updater => {
    setFormState(updater); setPreview(null); setError('');
  }, []);

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
        setJobs(data.jobs);
      } catch (reason) { if (reason.name !== 'AbortError') setError(reason.message); }
    }
    poll(); const timer = setInterval(poll, 1000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);

  function viewCoverage() {
    const request = requestFrom(form);
    setRange({ start_date: request.start_date, end_date: request.end_date, symbols: request.symbols });
  }
  async function generatePreview(request = requestFrom(form)) {
    setPending(true); setError(''); setPreview(null);
    try { setPreview(await api('/api/plans', request)); }
    catch (reason) { setError(reason.message); }
    finally { setPending(false); }
  }
  async function start(direct = false) {
    setPending(true); setError('');
    try {
      const plan = direct ? await api('/api/plans', requestFrom(form)) : preview;
      const job = await api('/api/jobs', { plan_id: plan.plan_id });
      setJobs(previous => [job, ...previous.filter(row => row.id !== job.id)]);
      setPreview(null); viewCoverage();
      document.getElementById('tasks')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (reason) { setError(reason.message); }
    finally { setPending(false); }
  }
  async function cancel(id) {
    try { await api(`/api/jobs/${id}/cancel`, {}); setJobs((await api('/api/jobs')).jobs); }
    catch (reason) { setError(reason.message); }
  }
  function retry(request) {
    setForm({ ...request, symbols: request.symbols.join(', ') });
    document.getElementById('update-title')?.scrollIntoView({ behavior: 'smooth' });
  }
  function syncCalendar() {
    const request = { dataset: 'calendar', mode: 'incremental', start_date: range.start_date, end_date: range.end_date, symbols: [] };
    setFormState({ ...request, symbols: '' });
    generatePreview(request);
    document.getElementById('update-title')?.scrollIntoView({ behavior: 'smooth' });
  }
  return <>
    <header><div className="header-inner"><a className="brand" href="/">SimTradeData<span>数据管理</span></a><a className="nav-link" href="#tasks">更新记录</a></div></header>
    <main><div className="page-heading"><div><h1>让历史数据保持完整</h1><p>查看本地覆盖，补齐缺口，核验每一次更新。</p></div><span className="range-label">{range.start_date} — {range.end_date}</span></div>
      <Coverage data={coverage} loading={loading} error={coverageError} onRefresh={() => setRefresh(value => value + 1)} onSyncCalendar={syncCalendar} />
      <UpdateForm form={form} setForm={setForm} preview={preview} onPreview={generatePreview} onStart={() => start(false)} onDownload={() => start(true)} pending={pending} active={Boolean(active)} error={error} onCoverage={viewCoverage} />
      <Tasks jobs={jobs} onCancel={cancel} onRetry={retry} />
      <footer>仅连接本机数据目录。最新快照不计入历史覆盖；未发布或不可获取的数据不会被填造。</footer>
    </main>
  </>;
}
