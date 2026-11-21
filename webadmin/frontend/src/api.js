export async function api(path, body, signal) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-SimTrade-UI': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

export const datasetNames = { daily: '日线行情', fundamentals: '季度财务', pcf: 'ETF申赎清单', calendar: '交易日历' };
export const modeNames = { auto: '智能补齐', incremental: '增量补充', refresh: '指定范围刷新', reparse: '缓存重新解析' };
export const number = (value) => new Intl.NumberFormat('zh-CN').format(value ?? 0);
