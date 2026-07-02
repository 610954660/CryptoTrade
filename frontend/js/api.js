// API 客户端 - 统一封装对后端的请求
const API_BASE = (window.API_BASE || '') + '/api';

async function _fetch(path, opts = {}) {
  const res = await fetch(API_BASE + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let detail = '';
    try { const j = await res.json(); detail = j.detail || j.message || ''; } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () => _fetch('/health'),
  status: () => _fetch('/status'),
  patterns: () => _fetch('/patterns'),
  indicators: (market = 'a_share') => _fetch(`/indicators?market=${encodeURIComponent(market)}`),

  aShareList: () => _fetch('/a-stocks/list'),
  aShareHs300: () => _fetch('/a-stocks/hs300'),
  aShareKline: (symbol, interval = '1d', limit = 200) =>
    _fetch(`/a-stocks/kline?symbol=${symbol}&interval=${interval}&limit=${limit}`),

  cryptoList: (provider = 'binance') => _fetch(`/crypto/list?provider=${provider}`),
  cryptoKline: (symbol, interval = '1h', limit = 200, provider = 'binance') =>
    _fetch(`/crypto/kline?symbol=${symbol}&interval=${interval}&limit=${limit}&provider=${provider}`),

  scan: (body) => _fetch('/scan', { method: 'POST', body: JSON.stringify(body) }),

  cacheStats: () => _fetch('/cache/stats'),
  cacheClear: (market = null) => _fetch('/cache/clear' + (market ? `?market=${market}` : ''), { method: 'POST' }),
  cacheWarmup: (body) => _fetch('/cache/warmup', { method: 'POST', body: JSON.stringify(body) }),
  cachePrewarm: () => _fetch('/cache/prewarm', { method: 'POST' }),
  cachePrewarmStatus: () => _fetch('/cache/prewarm/status'),
  cachePrewarmStop: () => _fetch('/cache/prewarm/stop', { method: 'POST' }),

  // 设置
  settingsGet: () => _fetch('/settings'),
  settingsPut: (body) => _fetch('/settings', { method: 'PUT', body: JSON.stringify(body) }),
  settingsTestProxy: () => _fetch('/settings/proxy/test', { method: 'POST' }),

  // 规则配置
  configsList: (market = null) =>
    _fetch('/configs' + (market ? `?market=${encodeURIComponent(market)}` : '')),
  configsCreate: (body) => _fetch('/configs', { method: 'POST', body: JSON.stringify(body) }),
  configsPatch: (id, body) => _fetch(`/configs/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  configsDelete: (id) => _fetch(`/configs/${id}`, { method: 'DELETE' }),
  configsSelect: (id) => _fetch('/configs/select', { method: 'POST', body: JSON.stringify({ id }) }),

  // 扫描筛选维度
  scanFilterOptions: (market) => _fetch('/scan/filter-options?market=' + encodeURIComponent(market)),
};
