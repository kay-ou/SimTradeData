import { datasetNames, modeNames, number } from './api';

export default function UpdateForm({ form, setForm, preview, onPreview, onStart, onDownload, pending, active, error, onCoverage }) {
  function change(key, value) {
    setForm(previous => ({ ...previous, [key]: value }));
  }
  function dataset(value) {
    setForm(previous => ({ ...previous, dataset: value,
      symbols: value === 'pcf' ? '159919.SZ' : '',
      mode: value === 'calendar' || (value === 'pcf' && previous.mode === 'reparse') ? 'incremental' : previous.mode,
    }));
  }
  return <section className="update-section" aria-labelledby="update-title">
    <div className="section-heading"><div><h2 id="update-title">下载与更新</h2><p>选择范围后直接开始下载，也可先查看预览。</p></div><span className="quiet-label">其他数据类别保持原样</span></div>
    <form onSubmit={event => { event.preventDefault(); onDownload(); }}>
      <div className="form-grid">
        <label>数据类别<select value={form.dataset} onChange={event => dataset(event.target.value)}>{Object.entries(datasetNames).map(([key, title]) => <option key={key} value={key}>{title}</option>)}</select></label>
        <label>更新方式<select value={form.mode} onChange={event => change('mode', event.target.value)}>{Object.entries(modeNames).map(([key, title]) => <option key={key} value={key} disabled={(form.dataset === 'pcf' && key === 'reparse') || (form.dataset === 'calendar' && key !== 'incremental')}>{title}</option>)}</select></label>
        <label>开始日期<input type="date" required value={form.start_date} onChange={event => change('start_date', event.target.value)} /></label>
        <label>结束日期<input type="date" required value={form.end_date} onChange={event => change('end_date', event.target.value)} /></label>
      </div>
      <div className="form-bottom">
        <label className="symbols">证券代码<input value={form.symbols} disabled={['fundamentals', 'calendar'].includes(form.dataset)} onChange={event => change('symbols', event.target.value)} placeholder={form.dataset === 'calendar' ? '交易日历无需证券代码' : form.dataset === 'fundamentals' ? '全市场季度包' : '留空更新全部证券，例如 600519.SS, 510300.SS'} />
          <span className="field-help">{form.dataset === 'calendar' ? '为日线与 PCF 的日期缺口提供检查基准。' : form.dataset === 'pcf' ? '历史清单目前支持深市 ETF；多个代码用逗号分隔。' : form.dataset === 'fundamentals' ? '一个季度包覆盖所有公司，保留已有未提供字段。' : '使用沪深历史包；日期范围内缺失记录可增量补齐。'}</span>
        </label>
        <div className="form-actions"><button type="button" className="secondary" onClick={onCoverage} disabled={pending}>查看此范围覆盖</button><button type="button" className="secondary" onClick={() => onPreview()} disabled={pending || active}>生成更新预览</button><button type="submit" disabled={pending || active}>{pending ? '正在准备…' : form.mode === 'reparse' ? '开始解析' : form.dataset === 'calendar' ? '开始同步' : '开始下载'}</button></div>
      </div>
    </form>
    {error && <p className="notice error" role="alert">{error}</p>}
    {active && <p className="notice">已有任务运行。可以继续查看数据；新任务需等待当前任务结束。</p>}
    {preview && <div className="preview" aria-label="更新预览">
      <div className="preview-title"><h3>{datasetNames[preview.request.dataset]} · {modeNames[preview.request.mode]}</h3><span>{preview.request.start_date} — {preview.request.end_date}</span></div>
      <p>{preview.note}</p>
      <div className="preview-metrics"><span><strong>{number(preview.unit_count)}</strong> 个处理单元</span><span><strong>{number(preview.existing_records)}</strong> 条范围内已有记录</span><span><strong>{number(preview.missing_dates)}</strong> 个已识别日期缺口</span></div>
      <div className="preview-footer"><span>{preview.scope}</span><button onClick={onStart} disabled={pending || active}>开始更新</button></div>
    </div>}
  </section>;
}
