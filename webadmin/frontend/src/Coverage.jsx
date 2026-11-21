import { number } from './api';

export default function Coverage({ data, loading, error, onRefresh, onSyncCalendar }) {
  return <section className="coverage" aria-labelledby="coverage-title">
    <div className="section-heading">
      <div><h2 id="coverage-title">本地数据覆盖</h2><p>统计所选日期范围。存在记录，不等于证券池或历史版本完整。</p></div>
      <button className="secondary" onClick={onRefresh} disabled={loading}>刷新覆盖</button>
    </div>
    {error && <p className="notice error" role="alert">{error}</p>}
    {data?.busy && <p className="notice">任务更新中，保留开始前的覆盖快照；完成后重新检查。</p>}
    {data && !data.busy && !data.calendar_available && <div className="notice calendar-notice"><span>所选范围没有本地交易日历，日线与 PCF 的日期缺口暂无法完整检查。</span><button className="secondary" onClick={onSyncCalendar}>同步交易日历</button></div>}
    {loading && !data ? <div className="loading" role="status">正在读取本地数据…</div> : <div className="table-scroll">
      <table><thead><tr><th>数据类别</th><th>实际日期范围</th><th>记录数</th><th>证券 / ETF</th><th>日期缺口</th><th>字段与归档</th></tr></thead>
        <tbody>{data?.datasets?.map(row => {
          const missing = row.null_fields?.reduce((sum, field) => sum + field.missing, 0) || 0;
          const absent = row.absent_fields?.length || 0;
          return <tr key={row.key}>
            <td><strong>{row.name}</strong><span className="cell-note">历史数据</span></td>
            <td className="dates">{row.first_date ? <>{row.first_date}<span className="cell-note">至 {row.last_date}</span></> : <span className="muted">尚无数据</span>}</td>
            <td className="numeric">{number(row.records)}</td><td className="numeric">{number(row.securities)}</td>
            <td>{row.missing_dates?.length ? <details><summary className="warning-text">{number(row.missing_dates.length)} 个缺口</summary><ul className="gap-list">{row.missing_dates.slice(0, 20).map(day => <li key={day}>{day}</li>)}</ul>{row.missing_dates.length > 20 && <span className="cell-note">仅展示前 20 个</span>}</details> : <span className="muted">{row.expected_dates ? '未发现整日缺口' : row.key === 'fundamentals' ? '范围内无季度期末' : row.key === 'pcf' && !row.securities ? '请选择 ETF 核查' : '暂无日历基准'}</span>}</td>
            <td>{row.key === 'pcf' ? <>{number(row.archives?.length)} 份清单<span className="cell-note">{row.invalid_archives?.length ? `${row.invalid_archives.length} 份归档异常` : '保留实际清单日期'}</span></> : <>
              <span className={missing || absent ? 'warning-text' : ''}>{!row.records ? '尚无可校验记录' : missing || absent ? `${number(missing)} 个关键字段缺失值${absent ? ` · ${absent} 列未建立` : ''}` : '关键字段未发现缺失'}</span>
              <span className="cell-note">{row.key === 'fundamentals' ? `已建 ${row.mapped_columns || 0} / ${row.supported_columns || 223} 个映射字段列` : '检查开高低收、量、额'}</span>
            </>}</td>
          </tr>;
        })}</tbody></table>
      {!data?.datasets?.length && !loading && <div className="loading">暂无覆盖结果</div>}
    </div>}
    <p className="footnote">日线缺口指交易日历中全市场无记录的日期；财务按季度期末统计；PCF 按 ETF × 交易日统计。字段缺失只检查六个关键字段。</p>
  </section>;
}
