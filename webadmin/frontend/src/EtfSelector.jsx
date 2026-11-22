import { t, translateError, locale } from './i18n';
import { useState } from 'react';
import { number } from './api';

export default function EtfSelector({ value, onChange, mode, catalogue }) {
  const { data, error, load } = catalogue;
  const [query, setQuery] = useState('');
  const [market, setMarket] = useState('all');
  const selected = [...new Set(value.split(/[,，\s]+/).filter(Boolean))];
  const tokens = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const items = (data?.etfs || []).filter(item => (market === 'all' || market === item.market) && tokens.every(token => `${item.symbol} ${item.name} ${item.short_name}`.toLowerCase().includes(token)));
  function toggle(symbol) {
    onChange(selected.includes(symbol) ? selected.filter(code => code !== symbol).join(', ') : [...selected, symbol].join(', '));
  }
  return <div className="etf-selector" aria-label={t("ETF选择器")}>
    <div className="etf-search"><label>{t("搜索ETF")}<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={t("输入代码或基金名称")} /></label><label>{t("ETF市场")}<select value={market} onChange={event => setMarket(event.target.value)}><option value="all">{t("沪深两市")}</option><option value="SZ">{t("深市")}</option><option value="SS">{t("沪市")}</option></select></label></div>
    <p className="field-help">{mode === 'daily' ? t("只在需要缩小更新范围时选择ETF；不选则更新全部股票与ETF。") : t("历史申赎清单目前支持深市查询；沪市仅接入最新清单。普通ETF回测请使用日线行情。")}</p>
    {(!data || data.running) && !error && <p role="status">{t("正在读取ETF目录…首次读取需要连接数据源。")}</p>}
    {(error || data?.error) && <p className="notice error" role="alert">{t("ETF目录读取失败：")}{translateError(error || data.error)}</p>}
    {!!data?.warnings?.length && <p className="notice">{t("当前目录可能不完整：")}{data.warnings.map(warning => t(warning)).join('; ')}</p>}
    <div className="etf-selection-summary"><span>{data && !data.running ? t("目录 {0} 只 · 当前搜索 {1} 只 · ", [number(data.total), number(items.length)]) : ''}{t("已选 ")}{selected.length} / 100</span><button type="button" className="text-button" disabled={!selected.length} onClick={() => onChange('')}>{t("清空选择")}</button><button type="button" className="text-button" disabled={data?.running} onClick={() => load(true)}>{t("刷新目录")}</button></div>
    {!!selected.length && <p className="selected-etfs">{t("已选代码：")}{selected.join(locale() === 'zh-CN' ? '、' : ', ')}</p>}
    <div className="etf-results">{items.map(item => {
      const checked = selected.includes(item.symbol);
      const allowed = mode === 'daily' || item.history_pcf_supported;
      return <label key={item.symbol} className={`etf-row ${!allowed ? 'unavailable' : ''}`}>
        <input type="checkbox" aria-label={`${item.symbol} ${item.short_name}`} checked={checked} disabled={!checked && (!allowed || selected.length >= 100)} onChange={() => toggle(item.symbol)} />
        <span><strong><span className="security-name">{item.short_name}</span> <span className="muted">{item.symbol}</span></strong>{item.name !== item.short_name && <span className="etf-full-name">{item.name}</span>}{mode === 'pcf' && <span className="etf-capability">{t(item.pcf_support)}</span>}<span className="field-help">{item.local_busy ? t("本地统计暂忙，任务结束后刷新") : item.local_daily ? t("本地已存 {0} 条：{1} 至 {2}", [number(item.local_daily.records), item.local_daily.first_date, item.local_daily.last_date]) : t("本地尚无日线记录，可在日线行情中下载")}</span></span>
      </label>;
    })}{data && !data.running && !data.error && !items.length && <p>{t("没有匹配的ETF，请修改搜索条件或刷新目录。")}</p>}</div>
    {data?.checked_at && <p className="field-help">{t("目录更新时间：")}{new Date(data.checked_at).toLocaleString(locale())}</p>}
    <p className="field-help">{t("目录展示当前来源可发现的ETF；接口支持和已有日期范围不代表连续历史完整，退市ETF未全面验收。")}</p>
  </div>;
}
