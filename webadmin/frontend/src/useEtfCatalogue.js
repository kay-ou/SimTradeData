import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

export default function useEtfCatalogue(revision) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const controllerRef = useRef(null);
  const load = useCallback(async (refresh = false) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setError('');
    try {
      let result = await api(refresh ? '/api/etfs/refresh' : '/api/etfs', refresh ? {} : undefined, controller.signal);
      while (!controller.signal.aborted) {
        setData(result);
        if (!result.running) break;
        await new Promise(resolve => setTimeout(resolve, 700));
        if (controller.signal.aborted) return;
        result = await api('/api/etfs', undefined, controller.signal);
      }
    } catch (reason) {
      if (reason.name !== 'AbortError') setError(reason.status === 404 ? '当前服务尚未加载ETF目录，请在任务结束后重启管理台。' : reason.message);
    }
  }, []);
  useEffect(() => { load(); return () => controllerRef.current?.abort(); }, [load, revision]);
  return { data, error, load };
}
