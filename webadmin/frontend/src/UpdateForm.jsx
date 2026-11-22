import { t, translateError } from './i18n';
import { useState } from 'react';
import { datasetNames, number } from './api';
import EtfSelector from './EtfSelector';

const descriptions = {
  daily: '股票和ETF一起更新：自动补齐缺少的行情，已有数据跳过。',
  fundamentals: '公司季度财报。自动检查缺失季度，已有文件优先复用。',
  pcf: 'ETF每日申赎篮子和现金信息，用于申赎与套利；历史清单目前支持深市查询。',
  calendar: '真实交易日，用于判断哪些日期应该有数据。',
  refresh: '强制刷新会重新下载并更新范围内已有数据。',
  reparse: '只使用已有缓存，重新解析所选日期范围。',
  repair: '只修补已有异常记录，无法确认的记录保留待核验。',
};

export default function UpdateForm({ autoRange, onAutoRange, recommendation, form, setForm, preview, onDownload, pending, active, error, onCoverage, onRepair, catalogue, lastJob, onTaskDetails, onRecheck }) {
  const [showEtfs, setShowEtfs] = useState(false);
  const selectionRequired = form.dataset === 'pcf' && !form.symbols.trim();
  const summary = preview?.update_summary;
  const checking = !selectionRequired && form.mode === 'auto' && (!preview || preview.checking_etfs) && !error && !active;
  const partial = lastJob?.request.dataset === form.dataset && lastJob.status === 'failed' && lastJob.result?.failed_units?.length > 0;
  function change(key, value) { setForm(previous => ({ ...previous, [key]: value })); }
  function selectDataset(value) {
    setShowEtfs(false);
    onAutoRange(true);
    setForm(previous => ({ ...previous, dataset: value, mode: 'auto', symbols: '' }));
  }
  function period(value) {
    const now = new Date();
    const start = value === 'all' ? '2015-01-01' : value === 'year' ? `${now.getFullYear()}-01-01` : new Date(now.getFullYear(), now.getMonth() - 3, now.getDate()).toLocaleDateString('sv-SE');
    setForm(previous => ({ ...previous, start_date: start, end_date: now.toLocaleDateString('sv-SE') }));
  }
  return <section className="simple-update" aria-labelledby="update-title">
    {form.dataset !== 'daily' && <button type="button" className="text-button return-daily" disabled={pending || active} onClick={() => selectDataset('daily')}>{t("返回股票与ETF行情")}</button>}
    <h2 id="update-title">{form.dataset === 'daily' ? t("股票与ETF行情") : t(datasetNames[form.dataset])}</h2>
    {(form.dataset !== 'daily' || form.mode !== 'auto') && <p className="dataset-description">{t(descriptions[form.mode]) || t(descriptions[form.dataset])}</p>}
    <form onSubmit={event => { event.preventDefault(); onDownload(); }}>
      {form.dataset === 'pcf' && <><EtfSelector value={form.symbols} onChange={value => change('symbols', value)} mode="pcf" catalogue={catalogue} /><details className="manual-etf"><summary>{t("手动输入ETF代码")}</summary><label>{t("ETF代码")}<input value={form.symbols} onChange={event => change('symbols', event.target.value)} placeholder={t("例如 159919.SZ，多个用逗号分隔")} /></label></details></>}
      {form.dataset === 'daily' && <div className="update-scope"><strong>{form.symbols.trim() ? t("仅更新 {0} 只所选证券", [form.symbols.split(/[,，\s]+/).filter(Boolean).length]) : t("更新范围：全部股票和ETF")}</strong>{form.symbols.trim() ? <button type="button" className="text-button" disabled={pending || active} onClick={() => change('symbols', '')}>{t("恢复更新全部")}</button> : null}</div>}
      <div className="smart-summary" aria-label={t("自动检查结果")}><strong>{selectionRequired ? t("请选择要获取清单的ETF") : active ? t("正在更新，请等待完成") : error ? t("暂时无法检查数据") : partial ? t("部分数据未获取") : checking ? t("正在检查本地数据…") : preview?.work_pending === false ? summary?.quality_issues ? t("日期已同步；{0} 条记录需核验", [number(summary.quality_issues)]) : t("已是最新") : preview ? form.mode === 'repair' ? t("{0} 条异常记录待修补", [number(summary?.quality_issues)]) : form.dataset === 'fundamentals' && summary ? t("{0} 个季度待补齐", [number(summary.pending_units)]) : form.dataset === 'pcf' && preview.needs_calendar ? t("先同步交易日历，再确定清单缺口") : form.dataset === 'pcf' && summary ? t("{0} 份清单待补齐", [number(summary.pending_units)]) : form.dataset === 'calendar' ? t("交易日历待同步") : t("有数据待补齐") : t("先检查本地数据，再决定是否下载")}</strong>
        {autoRange && recommendation && preview?.work_pending !== false && <p className="automatic-range">{form.start_date}{t(" 至 ")}{form.end_date}</p>}
        <p>{preview && form.dataset === 'fundamentals' && summary ? <>{t("优先复用 ")}<b>{number(summary.reuse_units)}</b>{t(" 个缓存包 · 需要下载 ")}<b>{number(summary.download_units)}</b>{t(" 个季度包")}</> : preview && form.dataset === 'pcf' && summary && !preview.needs_calendar ? <>{t("需要获取 ")}<b>{number(summary.download_units)}</b>{t(" 份清单，已有有效清单跳过。")}</> : partial ? t("已获取的数据已保存，未完成的证券可单独查看。") : preview && form.dataset === 'daily' ? preview.work_pending === false ? t("所选日期已同步，已有数据无需重复下载。") : t("只补缺少的数据，已有有效记录保留。") : preview ? <><b>{number(preview.existing_records)}</b>{t(" 条已有记录 · ")}<b>{number(preview.cached_files)}</b>{t(" 个本地缓存")}{preview.needs_calendar ? t(" · 更新时自动获取交易日历") : ''}</> : t("会优先使用已经下载的文件，保留已有有效数据。")}</p>
        {form.dataset !== 'daily' && <p className="field-help">{form.mode !== 'auto' ? t("已选择高级操作，将按下方设置更新范围内数据。") : form.dataset === 'daily' ? t("已有有效记录保留；无法获取的数据会在结果中说明。") : form.dataset === 'fundamentals' ? t("缓存包优先用于补齐；缓存若损坏会重新下载。已完整导入的季度跳过。") : form.dataset === 'pcf' ? t("先核对交易日历，已有有效的每日清单直接跳过。") : t("自动获取需要的真实交易日。")}</p>}
      </div>
      {partial && <button type="button" className="text-button" onClick={onTaskDetails}>{t("查看未完成名单")}</button>}
      {!!summary?.not_applicable_records && <p className="notice">{number(summary.not_applicable_records)}{t(" 条记录位于证券退出日及以后，已单独识别，不计作行情缺口。原记录保留。")}</p>}
      {!!summary?.quality_issues && <div className="notice"><p>{t("已有记录存在缺失或异常字段，智能更新不会因此重复下载整包。可定向修补，无法确认的记录会保留并报告。")}</p>{form.dataset === 'daily' && <><button type="button" className="secondary" disabled={pending || active} onClick={onRepair}>{t("修补不完整数据")}</button><button type="button" className="text-button" onClick={onCoverage}>{t("查看待核验数据")}</button></>}</div>}
      {error && <p className="notice error" role="alert">{translateError(error)}</p>}
      <div className="smart-actions"><button type={error ? "button" : "submit"} onClick={error ? onRecheck : undefined} className="primary-download" disabled={selectionRequired || pending || active || checking || (!error && preview?.work_pending === false)}>{selectionRequired ? t("请先选择ETF") : active ? t("正在更新，请稍候") : pending || checking ? t("正在检查本地数据…") : error ? t("重新检查") : preview?.work_pending === false ? t("已是最新") : form.mode === 'repair' ? t("定向修补异常记录") : form.mode === 'reparse' ? t("重新解析缓存") : form.mode === 'refresh' ? t("强制重新下载") : form.dataset === 'daily' ? form.symbols.trim() ? t("更新所选证券") : t("更新回测数据") : t("一键下载补齐")}</button><button type="button" className="text-button" onClick={onCoverage}>{t("查看本地数据详情")}</button></div>
      <p className="download-explanation">{autoRange ? t("日期自动检测。开始后会显示实时进度。") : t("按所选日期范围更新，进度显示在下方。")}</p>
      <details className="advanced-options"><summary>{t("高级选项：手动日期、指定证券、强制刷新")}</summary>
      {form.dataset === 'daily' && <details className="daily-etf-selection" onToggle={event => setShowEtfs(event.currentTarget.open)}><summary>{t("只更新指定ETF（可选；默认自动更新全部股票与ETF）")}</summary>{showEtfs && <EtfSelector value={form.symbols} onChange={value => change('symbols', value)} mode="daily" catalogue={catalogue} />}</details>}
        <label className="checkbox-field"><input type="checkbox" checked={!autoRange} onChange={event => onAutoRange(!event.target.checked)} />{t("手动指定回测范围")}</label>
        {!autoRange && <>
      <div className="simple-period"><strong>{t("需要哪些日期？")}</strong><div className="period-shortcuts"><button type="button" className="secondary" onClick={() => period('recent')}>{t("最近三个月")}</button><button type="button" className="secondary" onClick={() => period('year')}>{t("今年")}</button><button type="button" className="secondary" onClick={() => period('all')}>{t("2015年至今")}</button></div></div>
      <div className="date-pair"><label>{t("开始日期")}<input type="date" required value={form.start_date} onChange={event => change('start_date', event.target.value)} /></label><label>{t("结束日期")}<input type="date" required value={form.end_date} onChange={event => change('end_date', event.target.value)} /></label></div>
        </>}
        <label>{t("更新方式")}<select value={form.mode} onChange={event => { if (event.target.value !== 'auto') onAutoRange(false); change('mode', event.target.value); }}><option value="auto">{t("智能补齐（推荐，不重复下载已有数据）")}</option><option value="refresh" disabled={form.dataset === 'calendar'}>{t("强制重新下载（检查财报修订或替换旧数据）")}</option>{form.dataset === 'daily' && <option value="repair">{t("定向修补异常记录（缓存优先，备用源补充）")}</option>}<option value="reparse" disabled={['pcf', 'calendar'].includes(form.dataset)}>{t("只重新解析缓存")}</option></select></label>
        {form.dataset === 'daily' && <label>{t("证券代码")}<input value={form.symbols} onChange={event => change('symbols', event.target.value)} placeholder={t("留空处理全部股票和 ETF")} /></label>}
        <p className="field-help">{t("普通更新用智能补齐即可。只有需要追踪已下载财报的修订时才使用强制重新下载。")}</p>
      </details>
    </form>
    <div className="other-data"><span>{t("其他数据")}</span>{['fundamentals', 'pcf'].map(key => <button type="button" className={form.dataset === key ? 'text-button selected' : 'text-button'} aria-pressed={form.dataset === key} disabled={pending || active} key={key} onClick={() => selectDataset(key)}>{t(datasetNames[key])}</button>)}</div>
  </section>;
}
