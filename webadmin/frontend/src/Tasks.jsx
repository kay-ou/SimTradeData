import { datasetNames, modeNames, number } from './api';

const statuses = { running: '运行中', cancelling: '正在取消', completed: '已完成', failed: '失败 / 部分失败', cancelled: '已取消', interrupted: '服务中断' };
const stages = { prepare: '检查本地数据', download: '下载缺少的数据', write: '解析并保存', verify: '检查更新结果', interrupted: '中断' };
const bytes = value => `${((value || 0) / 1048576).toFixed(1)} MB`;

function Comparison({ job }) {
  if (!job.after) return null;
  if (job.request.mode === 'repair') return <div className="comparison"><span>已修复 <strong>{number(job.result?.written)}</strong> 条</span><span>仍待核验 <strong>{number(job.result?.unresolved)}</strong> 条</span></div>;
  if (job.request.dataset === 'calendar') return <div className="comparison">已同步 <strong>{number(job.result?.written || 0)}</strong> 个交易日</div>;
  const before = job.before.find(row => row.key === job.request.dataset);
  const after = job.after.find(row => row.key === job.request.dataset);
  if (!before || !after) return null;
  const missing = row => row.null_fields.reduce((total, field) => total + field.missing, 0);
  return <div className="comparison"><span>记录数 <strong>{number(before.records)} → {number(after.records)}</strong></span><span>日期缺口 <strong>{before.missing_dates === null ? '待核查' : number(before.missing_dates)} → {number(after.missing_dates)}</strong></span><span>关键字段缺失 <strong>{number(missing(before))} → {number(missing(after))}</strong></span><span>写入 / 刷新 <strong>{number(job.result?.written || 0)}</strong> 条</span></div>;
}

export default function Tasks({ jobs, onCancel, onRetry }) {
  if (!jobs.length) return null;
  jobs = jobs.slice(0, 1);
  return <section className="tasks" id="tasks" aria-labelledby="tasks-title">
    <div className="section-heading"><div><h2 id="tasks-title">当前任务</h2><p>仅显示本次进度与结果，不保存更新历史。</p></div></div>
    {!jobs.length ? <div className="empty-state"><h3>还没有更新任务</h3><p>选择数据和日期，生成预览后即可开始。</p></div> : <div className="task-list">{jobs.map(job => {
      const active = ['running', 'cancelling'].includes(job.status);
      const overall = job.overall_total > 0;
      const verifying = job.stage === 'verify';
      const downloading = job.stage === 'download' && job.downloaded_bytes !== undefined;
      const maximum = downloading ? job.total_bytes : job.total;
      const completed = downloading ? job.downloaded_bytes : job.completed;
      return <article className="task" key={job.id}>
        <div className="task-heading"><div><h3>{datasetNames[job.request.dataset]} <span className="muted">/ {modeNames[job.request.mode]}</span></h3><p>{job.request.start_date} — {job.request.end_date} <span className="muted">· {new Date(job.started_at).toLocaleString('zh-CN', { hour12: false })}</span></p></div><span className={`task-status ${job.status}`}>{statuses[job.status]}</span></div>
        {active && overall && <div className="overall-progress"><div className="progress-label"><span>{verifying ? '正在核验更新结果，任务尚未完成' : '整体处理进度'}</span><strong>{verifying ? '校验中' : `${number(job.overall_completed)} / ${number(job.overall_total)} ${job.unit_label || '季度'}`}</strong></div><progress aria-label="整体处理进度" max={job.overall_total} value={verifying ? undefined : job.overall_completed} />{!job.current_unit && <p className="field-help">{job.message}</p>}</div>}
        {active && (!overall || job.current_unit || downloading) && !verifying && <div className="live-progress"><div className="progress-label"><span>{job.current_unit ? `当前季度 ${job.current_unit} · ` : ''}{stages[job.stage] || job.stage} · {job.message}</span><strong>{maximum ? `${Math.min(100, completed / maximum * 100).toFixed(1)}%` : downloading ? '传输中' : '处理中'}</strong></div><progress aria-label="任务进度" max={maximum || 1} value={maximum ? completed : undefined} /><div className="progress-detail"><span>{downloading ? `${bytes(job.downloaded_bytes)} / ${job.total_bytes ? bytes(job.total_bytes) : '总大小待服务器提供'}` : job.current_unit ? `${number(job.completed)} / ${number(job.total)} 家公司` : `${number(job.completed)} / ${number(job.total)} 个处理单元`}</span><span>{downloading && job.bytes_per_second ? `${bytes(job.bytes_per_second)}/s` : ''}</span></div></div>}
        {active && verifying && !overall && <p className="notice">正在核验更新结果，任务尚未完成。</p>}
        <Comparison job={job} />
        {!!job.result?.failed_units?.length && <details className="failures"><summary>{number(job.result.failed_units.length)} 个单元未完成</summary><ul>{job.result.failed_units.slice(0, 30).map(row => <li key={row.unit}><strong>{row.unit}</strong> · {row.detail}</li>)}</ul></details>}
        <div className="task-footer"><details className="logs"><summary>查看日志</summary><pre>{job.logs.length ? job.logs.join('\n') : job.message}</pre></details>{active ? <button className="secondary danger" onClick={() => onCancel(job.id)} disabled={job.status === 'cancelling'}>取消任务</button> : <button className="secondary" onClick={() => onRetry(job.request)}>使用此任务参数</button>}</div>
      </article>;
    })}</div>}
  </section>;
}
