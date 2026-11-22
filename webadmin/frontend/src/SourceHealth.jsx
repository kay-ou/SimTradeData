import { t, translateError, locale } from './i18n';
import { useEffect, useState } from 'react';
import { api } from './api';

const statusNames = { unchecked: '尚未检查', retry: '待复查', unhealthy: '异常' };
const freshnessNames = { update: '待更新', stale: '源数据滞后', current: '正常', unknown: '新鲜度待核对' };

export default function SourceHealth() {
  const [state, setState] = useState(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    async function poll() {
      if (document.hidden) return;
      try {
        setState(await api('/api/source-health', undefined, controller.signal));
        setError(''); setUnavailable(false);
      } catch (reason) {
        if (reason.status === 404) setUnavailable(true);
        else if (reason.name !== 'AbortError') setError(reason.message);
      }
    }
    poll();
    const timer = setInterval(poll, 2000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);
  async function check() {
    setPending(true);
    try { setState(await api('/api/source-health/check', {})); setError(''); }
    catch (reason) { setError(reason.message); }
    finally { setPending(false); }
  }
  return <section className="source-health" aria-label={t("数据源健康度")}>
    <div className="source-health-heading"><h2>{t("数据源状态")}</h2><button type="button" className="secondary" disabled={unavailable || pending || state?.running} onClick={check}>{pending || state?.running ? t("正在检查数据源…") : t("检查数据源")}</button></div>
    {unavailable && <p className="notice">{t("当前服务尚未加载检查功能。请等待更新任务完成后重启管理台。")}</p>}
    <p className="field-help">{t("管理台运行时每天检查一次。只获取目录和少量样本，连续失败三次标为异常。")}</p>
    {error && <p role="alert" className="notice error">{translateError(error)}</p>}
    <div className="source-health-rows">{state?.sources.map(source => <details key={source.key}>
      <summary><span>{t(source.name)}</span><strong>{source.status === 'healthy' ? t(freshnessNames[source.freshness]) : t(statusNames[source.status])}</strong></summary>
      <p>{t(source.detail) || t("等待首次检查。")}</p>
      <p className="field-help">{source.latest ? t("源最新日期／包发布时间：{0} · ", [source.latest]) : ''}{source.checked_at ? t("检查时间：{0}", [new Date(source.checked_at).toLocaleString(locale())]) : ''}{source.failures ? t(" · 连续失败 {0} 次", [source.failures]) : ''}</p>
    </details>)}</div>
  </section>;
}
