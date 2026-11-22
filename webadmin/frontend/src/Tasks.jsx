import { t, locale } from './i18n';
import { datasetNames, modeNames, number } from './api';

const statuses = { running: '运行中', cancelling: '正在取消', completed: '已完成', failed: '失败 / 部分失败', cancelled: '已取消', interrupted: '服务中断' };
const stages = { prepare: '检查本地数据', download: '下载缺少的数据', write: '解析并保存', verify: '检查更新结果', interrupted: '中断' };
const bytes = value => `${((value || 0) / 1048576).toFixed(1)} MB`;

function Comparison({ job }) {
  if (!job.after) return null;
  if (job.request.mode === 'repair') return <div className="comparison"><span>{t("已修复 ")}<strong>{number(job.result?.written)}</strong>{t(" 条")}</span><span>{t("仍待核验 ")}<strong>{number(job.result?.unresolved)}</strong>{t(" 条")}</span>{!!job.result?.not_applicable && <span>{t("退出后空记录 ")}<strong>{number(job.result.not_applicable)}</strong>{t(" 条")}</span>}</div>;
  if (job.request.dataset === 'calendar') return <div className="comparison">{t("已同步 ")}<strong>{number(job.result?.written || 0)}</strong>{t(" 个交易日")}</div>;
  const before = job.before.find(row => row.key === job.request.dataset);
  const after = job.after.find(row => row.key === job.request.dataset);
  if (!before || !after) return null;
  if (job.request.dataset === 'daily' && after.categories) return <div className="comparison">{after.categories.filter(row => row.key !== 'other_daily' || row.records).map(row => {
    const previous = before.categories?.find(category => category.key === row.key);
    return <span key={row.key}>{t(row.name)}{t("记录 ")}<strong>{number(previous?.records || 0)} → {number(row.records)}</strong><span className="cell-note">{number(row.securities)}{t(" 只 · ")}{row.first_date || t("尚无数据")}{row.last_date ? t(" 至 {0}", [row.last_date]) : ''}</span></span>;
  })}<span>{t("写入 / 刷新 ")}<strong>{number(job.result?.written || 0)}</strong>{t(" 条")}</span></div>;
  const missing = row => row.null_fields.reduce((total, field) => total + field.missing, 0);
  return <div className="comparison"><span>{t("记录数 ")}<strong>{number(before.records)} → {number(after.records)}</strong></span><span>{t("日期缺口 ")}<strong>{before.missing_dates === null ? t("待核查") : number(before.missing_dates)} → {number(after.missing_dates)}</strong></span><span>{t("关键字段缺失 ")}<strong>{number(missing(before))} → {number(missing(after))}</strong></span><span>{t("写入 / 刷新 ")}<strong>{number(job.result?.written || 0)}</strong>{t(" 条")}</span></div>;
}

export default function Tasks({ jobs, onCancel, onRetry }) {
  if (!jobs.length) return null;
  jobs = jobs.slice(0, 1);
  return <section className="tasks" id="tasks" aria-labelledby="tasks-title">
    <div className="section-heading"><div><h2 id="tasks-title">{t("当前任务")}</h2><p>{t("仅显示本次进度与结果，不保存更新历史。")}</p></div></div>
    {!jobs.length ? <div className="empty-state"><h3>{t("还没有更新任务")}</h3><p>{t("选择数据和日期，生成预览后即可开始。")}</p></div> : <div className="task-list">{jobs.map(job => {
      const active = ['running', 'cancelling'].includes(job.status);
      const overall = job.overall_total > 0;
      const verifying = job.stage === 'verify';
      const downloading = job.stage === 'download' && job.downloaded_bytes !== undefined;
      const maximum = downloading ? job.total_bytes : job.total;
      const completed = downloading ? job.downloaded_bytes : job.completed;
      return <article className="task" key={job.id}>
        <div className="task-heading"><div><h3>{t(datasetNames[job.request.dataset])} <span className="muted">/ {t(modeNames[job.request.mode])}</span></h3><p>{job.request.start_date} — {job.request.end_date}{!!job.request.symbols?.length && <span className="cell-note">{t("本次仅处理 ")}{job.request.symbols.length}{t(" 只所选证券")}</span>} <span className="muted">· {new Date(job.started_at).toLocaleString(locale(), { hour12: false })}</span></p></div><span className={`task-status ${job.status}`}>{t(statuses[job.status])}</span></div>
        {active && overall && <div className="overall-progress"><div className="progress-label"><span>{verifying ? t("正在核验更新结果，任务尚未完成") : t("整体处理进度")}</span><strong>{verifying ? t("校验中") : `${number(job.overall_completed)} / ${number(job.overall_total)} ${t(job.unit_label) || t("季度")}`}</strong></div><progress aria-label={t("整体处理进度")} max={job.overall_total} value={verifying ? undefined : job.overall_completed} />{!job.current_unit && <p className="field-help">{t(job.message)}</p>}</div>}
        {active && (!overall || job.current_unit || downloading) && !verifying && <div className="live-progress"><div className="progress-label"><span>{job.current_unit ? t("当前季度 {0} · ", [job.current_unit]) : ''}{t(stages[job.stage]) || job.stage} · {t(job.message)}</span><strong>{maximum ? `${Math.min(100, completed / maximum * 100).toFixed(1)}%` : downloading ? t("传输中") : t("处理中")}</strong></div><progress aria-label={t("任务进度")} max={maximum || 1} value={maximum ? completed : undefined} /><div className="progress-detail"><span>{downloading ? `${bytes(job.downloaded_bytes)} / ${job.total_bytes ? bytes(job.total_bytes) : t("总大小待服务器提供")}` : job.current_unit ? t("{0} / {1} 家公司", [number(job.completed), number(job.total)]) : t("{0} / {1} 个处理单元", [number(job.completed), number(job.total)])}</span><span>{downloading && job.bytes_per_second ? `${bytes(job.bytes_per_second)}/s` : !downloading && job.written !== undefined ? t("已保存 {0} 条", [number(job.written)]) : ''}</span></div></div>}
        {active && verifying && !overall && <p className="notice">{t("正在核验更新结果，任务尚未完成。")}</p>}
        {!active && <p className={job.status === 'failed' ? 'notice error' : 'notice'}>{job.status === 'completed' ? t("本次更新已完成，已有有效数据保留。") : job.status === 'failed' ? job.result?.failed_units?.length ? t("{0} 个单元未完成；已获取并通过校验的数据已保存。", [job.result.failed_units.length]) : t("本次更新未完成，请查看错误详情后重试。") : t("更新已停止，已保存的数据保留。")}</p>}
        <Comparison job={job} />
        {!!job.result?.not_trading?.length && <details className="lifecycle-notes"><summary>{number(job.result.not_trading.length)}{t(" 个请求无对应的独立上市行情，不计作下载失败")}</summary><ul>{job.result.not_trading.map(row => <li key={row.symbol}><strong>{row.symbol}</strong> · {t(row.detail)}{row.source_url && <> · <a href={row.source_url} target="_blank" rel="noreferrer">{t("查看依据")}</a></>}</li>)}</ul></details>}
        {!!job.result?.lifecycle_notes?.length && <details className="lifecycle-notes"><summary>{number(job.result.not_applicable)}{t(" 条记录已按证券生命周期核验")}</summary><ul>{job.result.lifecycle_notes.map(row => <li key={`${row.symbol}-${row.date}`}>{row.symbol} · {row.date} · {t(row.detail)}</li>)}</ul></details>}
        {!!job.result?.failed_units?.length && <details className="failures"><summary>{number(job.result.failed_units.length)}{t(" 个单元未完成")}</summary><ul>{job.result.failed_units.slice(0, 30).map(row => <li key={row.unit}><strong>{t(row.unit)}</strong> · {t(row.detail)}{!!row.missing_dates?.length && <span className="cell-note">{t("仍未获取的日期：")}{row.missing_dates.slice(0, 10).join(', ')}{row.missing_dates.length > 10 ? t(" 等 {0} 个日期", [row.missing_dates.length]) : ''}</span>}{row.source_errors && Object.keys(row.source_errors).length > 0 && <details className="source-diagnostics"><summary>{t("查看数据源诊断（原文）")}</summary><pre>{JSON.stringify(row.source_errors, null, 2)}</pre></details>}</li>)}</ul></details>}
        <div className="task-footer"><details className="logs"><summary>{t("查看日志")}</summary><pre>{job.logs.length ? job.logs.join('\n') : job.message}</pre></details>{active ? <button className="secondary danger" onClick={() => onCancel(job.id)} disabled={job.status === 'cancelling'}>{t("取消任务")}</button> : <button className="secondary" onClick={() => onRetry(job.request, job.status !== 'completed', job.result)}>{job.status === 'completed' ? t("调整本次范围") : t("继续补齐")}</button>}</div>
      </article>;
    })}</div>}
  </section>;
}
