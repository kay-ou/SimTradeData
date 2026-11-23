import { useEffect, useState } from 'react';
import { api, number } from './api';
import { t, locale } from './i18n';

export default function ExportPanel({ active, pending, job, refresh, onExport, onCancel }) {
  const [market, setMarket] = useState('cn');
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api('/api/exports', undefined, controller.signal).then(setStatus)
      .catch(reason => { if (reason.name !== 'AbortError') setError(reason.message); });
    return () => controller.abort();
  }, [refresh, job?.status]);
  const selected = status?.markets.find(row => row.market === market);
  const size = selected?.artifact ? `${number(Number((selected.artifact.bytes / (selected.artifact.bytes < 1048576 ? 1024 : 1048576)).toFixed(1)))} ${selected.artifact.bytes < 1048576 ? 'KB' : 'MB'}` : '';
  const exporting = job && ['running', 'cancelling'].includes(job.status);
  async function start() {
    setError('');
    try { await onExport(market); }
    catch (reason) { setError(reason.message); }
  }
  return <section className="export-panel" aria-labelledby="export-title">
    <div className="section-heading"><div><h2 id="export-title">{t('导出回测数据包')}</h2><p>{t('导出所选市场全部已保存历史，不受上方更新范围限制。')}</p></div></div>
    <p className="field-help">{t('Parquet 格式，包含行情、除权除息、财务、估值和元数据；A股包同时包含已保存的ETF与PCF。')}</p>
    <div className="export-controls"><label>{t('导出市场')}<select value={market} onChange={event => setMarket(event.target.value)} disabled={exporting || pending}><option value="cn">{t('沪深股票与ETF')}</option><option value="us">{t('美股')}</option></select></label>
      <button type="button" onClick={start} disabled={Boolean(active) || pending || !selected?.available}>{pending ? t('正在准备…') : t('生成数据包')}</button>
      {selected?.artifact && <a className="secondary" href={selected.artifact.download_url} download>{t('下载数据包')} · {size}</a>}
    </div>
    {selected && !selected.available && <p className="field-help">{t('所选市场尚无数据库，请先获取数据。')}</p>}
    {selected?.artifact && <p className="field-help">{t('上次生成：')}{new Date(selected.artifact.created_at).toLocaleString(locale())}{t('。数据更新后请重新生成数据包。')}</p>}
    <p className="field-help">{t('用于 SimTradeLab / SimTradeDesk；解压时保留包内目录结构。')}</p>
    <p className="field-help">{t('缺失且未经核验的交易日不会填造成停牌行情；导出成功不代表历史覆盖完整。')}</p>
    {error && <p role="alert" className="notice error">{t(error)}</p>}
    {job?.request.market === market && <div className="export-progress">
      <p className={job.status === 'failed' ? 'notice error' : 'notice'}>{t(job.message)}</p>
      {exporting && <><div className="progress-label"><span>{t('导出阶段进度')}</span><strong>{number(Math.floor(job.completed || 0))} / {number(job.total)}</strong></div><progress aria-label={t('导出阶段进度')} max={job.total || 7} value={job.completed || 0} /><button className="secondary danger" onClick={() => onCancel(job.id)} disabled={job.status === 'cancelling'}>{t('取消任务')}</button></>}
      {job.status === 'completed' && job.result && <p>{job.result.date_range.start} — {job.result.date_range.end} · {job.result.market === 'cn' ? t('股票 {0} 只 · ETF {1} 只', [number(job.result.files.stocks), number(job.result.files.etfs)]) : t('行情文件 {0} 个', [number(job.result.files.stocks + job.result.files.etfs)])}</p>}
      {!!job.logs?.length && <details className="logs"><summary>{t('查看日志')}</summary><pre>{job.logs.join('\n')}</pre></details>}
    </div>}
  </section>;
}
