import { t, translateError } from './i18n';
import { useEffect, useState } from 'react';
import { api } from './api';

export default function RestartButton({ active }) {
  const [supported, setSupported] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api('/api/service', undefined, controller.signal).then(data => setSupported(data.restart_supported)).catch(() => {});
    return () => controller.abort();
  }, []);
  async function restart() {
    setRestarting(true); setError('');
    try {
      const previous = await api('/api/service/restart', {});
      const deadline = Date.now() + 30000;
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 500));
        try {
          const current = await api('/api/service', undefined, AbortSignal.timeout(2000));
          if (current.instance_id !== previous.instance_id) { window.location.reload(); return; }
        } catch { /* The listener is briefly unavailable during restart. */ }
      }
      throw new Error(t("管理台尚未恢复连接，请查看启动终端后刷新页面。"));
    } catch (reason) { setError(reason.message); setRestarting(false); }
  }
  return <div className="restart-control">
    <button className="secondary" type="button" disabled={!supported || active || restarting} onClick={restart} title={active ? t("请先完成或取消当前任务") : !supported ? t("当前服务需先手动重启一次，才能加载重启功能") : t("重新加载后端代码，已保存的数据保留")}>{restarting ? t("正在重启…") : t("重启管理台")}</button>
    {error && <p className="notice error" role="alert">{translateError(error)}</p>}
  </div>;
}
