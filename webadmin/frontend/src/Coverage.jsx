import { t, translateError } from './i18n';
import { number } from './api';

export default function Coverage({ data, loading, error, onRefresh, onSyncCalendar }) {
  return <section className="coverage" aria-labelledby="coverage-title">
    <div className="section-heading">
      <div><h2 id="coverage-title">{t("本地数据覆盖")}</h2><p>{t("统计所选日期范围。存在记录，不等于证券池或历史版本完整。")}</p></div>
      <button className="secondary" onClick={onRefresh} disabled={loading}>{t("刷新覆盖")}</button>
    </div>
    {error && <p className="notice error" role="alert">{translateError(error)}</p>}
    {data?.busy && <p className="notice">{t("任务更新中，保留开始前的覆盖快照；完成后重新检查。")}</p>}
    {data && !data.busy && !data.calendar_available && <div className="notice calendar-notice"><span>{t("所选范围没有本地交易日历，日线与 PCF 的日期缺口暂无法完整检查。")}</span><button className="secondary" onClick={onSyncCalendar}>{t("同步交易日历")}</button></div>}
    {loading && !data ? <div className="loading" role="status">{t("正在读取本地数据…")}</div> : <div className="table-scroll">
      <table><thead><tr><th>{t("数据类别")}</th><th>{t("实际日期范围")}</th><th>{t("记录数")}</th><th>{t("证券 / ETF")}</th><th>{t("日期缺口")}</th><th>{t("字段与归档")}</th></tr></thead>
        <tbody>{data?.datasets?.flatMap(row => row.key === 'daily' && row.categories ? row.categories.filter(category => category.key !== 'other_daily' || category.records) : [row]).map(row => {
          const missing = row.null_fields?.reduce((sum, field) => sum + field.missing, 0) || 0;
          const absent = row.absent_fields?.length || 0;
          return <tr key={row.key}>
            <td><strong>{t(row.name)}</strong><span className="cell-note">{t("历史数据")}</span></td>
            <td className="dates">{row.first_date ? <>{row.first_date}<span className="cell-note">{t("至 ")}{row.last_date}</span></> : <span className="muted">{t("尚无数据")}</span>}</td>
            <td className="numeric">{number(row.records)}</td><td className="numeric">{number(row.securities)}</td>
            <td>{row.missing_dates?.length ? <details><summary className="warning-text">{number(row.missing_dates.length)}{t(" 个缺口")}</summary><ul className="gap-list">{row.missing_dates.slice(0, 20).map(day => <li key={day}>{day}</li>)}</ul>{row.missing_dates.length > 20 && <span className="cell-note">{t("仅展示前 20 个")}</span>}</details> : <span className="muted">{row.expected_dates ? t("未发现整日缺口") : row.key === 'fundamentals' ? t("范围内无季度期末") : row.key === 'pcf' && !row.securities ? t("请选择 ETF 核查") : t("暂无日历基准")}</span>}</td>
            <td>{row.key === 'pcf' ? <>{number(row.archives?.length)}{t(" 份清单")}<span className="cell-note">{row.invalid_archives?.length ? t("{0} 份归档异常", [row.invalid_archives.length]) : t("保留实际清单日期")}</span></> : <>
              <span className={missing || absent ? 'warning-text' : ''}>{!row.records ? t("尚无可校验记录") : missing || absent ? t("{0} 个关键字段缺失值{1}", [number(missing), absent ? t(" · {0} 列未建立", [absent]) : '']) : t("关键字段未发现缺失")}</span>
              <span className="cell-note">{row.key === 'fundamentals' ? t("已建 {0} / {1} 个映射字段列", [row.mapped_columns || 0, row.supported_columns || 223]) : t("检查开高低收、量、额")}</span>
            </>}</td>
          </tr>;
        })}</tbody></table>
      {!data?.datasets?.length && !loading && <div className="loading">{t("暂无覆盖结果")}</div>}
    </div>}
    <p className="footnote">{t("股票、ETF及其他行情分别统计；日期缺口表示该类别在交易日历中整日无记录，并非逐只证券的缺口；财务按季度期末统计；PCF 按 ETF × 交易日统计。字段缺失只检查六个关键字段。")}</p>
  </section>;
}
