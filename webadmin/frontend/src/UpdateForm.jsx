import { datasetNames, number } from './api';

const descriptions = {
  daily: '股票和 ETF 的每日开盘、收盘、成交量，用于历史回测。',
  fundamentals: '公司季度财报。自动检查缺失季度，已有文件优先复用。',
  pcf: 'ETF 每日申购、赎回的股票篮子和现金信息，目前支持深市 ETF。',
  calendar: '真实交易日，用于判断哪些日期应该有数据。',
};

export default function UpdateForm({ autoRange, onAutoRange, recommendation, form, setForm, preview, onDownload, pending, active, error, onCoverage, onRepair }) {
  const summary = preview?.update_summary;
  const checking = form.mode === 'auto' && !preview && !error && !active;
  function change(key, value) { setForm(previous => ({ ...previous, [key]: value })); }
  function selectDataset(value) {
    onAutoRange(true);
    setForm(previous => ({ ...previous, dataset: value, mode: 'auto', symbols: value === 'pcf' ? '159919.SZ' : '' }));
  }
  function period(value) {
    const now = new Date();
    const start = value === 'all' ? '2015-01-01' : value === 'year' ? `${now.getFullYear()}-01-01` : new Date(now.getFullYear(), now.getMonth() - 3, now.getDate()).toLocaleDateString('sv-SE');
    setForm(previous => ({ ...previous, start_date: start, end_date: now.toLocaleDateString('sv-SE') }));
  }
  return <section className="simple-update" aria-labelledby="update-title">
    <h2 id="update-title">你想更新什么数据？</h2>
    <div className="dataset-picker">{['daily', 'fundamentals', 'pcf'].map(key => <button type="button" className={form.dataset === key ? 'dataset-option selected' : 'dataset-option'} aria-pressed={form.dataset === key} key={key} onClick={() => selectDataset(key)}>{datasetNames[key]}</button>)}</div>
    <p className="dataset-description">{descriptions[form.dataset]}</p>
    <form onSubmit={event => { event.preventDefault(); onDownload(); }}>
      {autoRange && <div className="automatic-range"><strong>{active ? '更新任务运行中，完成后自动检查缺口' : preview?.work_pending === false ? '未检测到待补齐日期' : recommendation ? '自动检测到待补齐范围' : '正在检测数据缺口…'}</strong>
        {recommendation && preview?.work_pending !== false && <p>{form.start_date} 至 {form.end_date} · 仅补其中缺口，已有有效数据跳过</p>}
        <p className="field-help">{recommendation?.explanation}</p>
      </div>}
      {form.dataset === 'pcf' && <label className="etf-input">ETF代码<input required value={form.symbols} onChange={event => change('symbols', event.target.value)} placeholder="例如 159919.SZ" /><span className="field-help">多个 ETF 用逗号分隔，已有清单会自动跳过。</span></label>}
      <div className="smart-summary" aria-label="自动检查结果"><strong>{preview?.work_pending === false ? summary?.quality_issues ? `日期已同步；${number(summary.quality_issues)} 条记录需核验` : '当前范围已同步，无需更新' : preview ? form.mode === 'repair' ? `${number(summary?.quality_issues)} 条异常记录待修补` : form.dataset === 'fundamentals' && summary ? `${number(summary.pending_units)} 个季度待补齐` : form.dataset === 'pcf' && preview.needs_calendar ? '先同步交易日历，再确定清单缺口' : form.dataset === 'pcf' && summary ? `${number(summary.pending_units)} 份清单待补齐` : form.dataset === 'calendar' ? '交易日历待同步' : '日线存在日期缺口，需要补齐' : '先检查本地数据，再决定是否下载'}</strong>
        <p>{preview && form.dataset === 'fundamentals' && summary ? <>优先复用 <b>{number(summary.reuse_units)}</b> 个缓存包 · 需要下载 <b>{number(summary.download_units)}</b> 个季度包</> : preview && form.dataset === 'pcf' && summary && !preview.needs_calendar ? <>需要获取 <b>{number(summary.download_units)}</b> 份清单，已有有效清单跳过。</> : preview ? <><b>{number(preview.existing_records)}</b> 条已有记录 · <b>{number(preview.cached_files)}</b> 个本地缓存{preview.needs_calendar ? ' · 更新时自动获取交易日历' : ''}</> : '会优先使用已经下载的文件，保留已有有效数据。'}</p>
        <p className="field-help">{form.mode !== 'auto' ? '已选择高级操作，将按下方设置更新范围内数据。' : form.dataset === 'daily' ? '缓存覆盖日期就直接使用，只补缺失记录和无效字段。缓存不够新时才下载新文件。' : form.dataset === 'fundamentals' ? '缓存包优先用于补齐；缓存若损坏会重新下载。已完整导入的季度跳过。' : form.dataset === 'pcf' ? '先核对交易日历，已有有效的每日清单直接跳过。' : '自动获取需要的真实交易日。'}</p>
      </div>
      {!!summary?.quality_issues && <div className="notice"><p>已有记录存在缺失或异常字段，智能更新不会因此重复下载整包。可定向修补，无法确认的记录会保留并报告。</p>{form.dataset === 'daily' && <button type="button" className="secondary" disabled={pending || active} onClick={onRepair}>修补不完整数据</button>}</div>}
      {error && <p className="notice error" role="alert">{error}</p>}
      <div className="smart-actions"><button type="submit" className="primary-download" disabled={pending || active || checking || preview?.work_pending === false}>{active ? '正在更新，请稍候' : pending || checking ? '正在检查本地数据…' : preview?.work_pending === false ? '已同步，无需更新' : error ? '重新检查' : form.mode === 'repair' ? '定向修补异常记录' : form.mode === 'reparse' ? '重新解析缓存' : form.mode === 'refresh' ? '强制重新下载' : '一键下载补齐'}</button><button type="button" className="text-button" onClick={onCoverage}>查看本地数据详情</button></div>
      <p className="download-explanation">无需手动判断下载方式。任务开始后，下方会显示实时进度和处理结果。</p>
      <details className="advanced-options"><summary>高级选项：手动日期、指定证券、强制刷新</summary>
        <label><input type="checkbox" checked={!autoRange} onChange={event => onAutoRange(!event.target.checked)} />手动指定回测范围</label>
        {!autoRange && <>
      <div className="simple-period"><strong>需要哪些日期？</strong><div className="period-shortcuts"><button type="button" className="secondary" onClick={() => period('recent')}>最近三个月</button><button type="button" className="secondary" onClick={() => period('year')}>今年</button><button type="button" className="secondary" onClick={() => period('all')}>2015年至今</button></div></div>
      <div className="date-pair"><label>开始日期<input type="date" required value={form.start_date} onChange={event => change('start_date', event.target.value)} /></label><label>结束日期<input type="date" required value={form.end_date} onChange={event => change('end_date', event.target.value)} /></label></div>
        </>}
        <label>更新方式<select value={form.mode} onChange={event => { if (event.target.value !== 'auto') onAutoRange(false); change('mode', event.target.value); }}><option value="auto">智能补齐（推荐，不重复下载已有数据）</option><option value="refresh" disabled={form.dataset === 'calendar'}>强制重新下载（检查财报修订或替换旧数据）</option>{form.dataset === 'daily' && <option value="repair">定向修补异常记录（缓存优先，备用源补充）</option>}<option value="reparse" disabled={['pcf', 'calendar'].includes(form.dataset)}>只重新解析缓存</option></select></label>
        {form.dataset === 'daily' && <label>证券代码<input value={form.symbols} onChange={event => change('symbols', event.target.value)} placeholder="留空处理全部股票和 ETF" /></label>}
        <p className="field-help">普通更新用智能补齐即可。只有需要追踪已下载财报的修订时才使用强制重新下载。</p>
      </details>
    </form>
  </section>;
}
