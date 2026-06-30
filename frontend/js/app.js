// 应用主入口 - 串起所有 UI 与状态
import { api } from './api.js';
import { FilterManager, INTERVALS } from './filters.js';
import { KLineChart } from './chart.js';
import { openSettings } from './settings.js';

const state = {
  market: 'crypto_okx',     // 当前选中的市场 (默认 OKX, 大陆可直连)
  provider: 'okx',          // crypto 时的子 provider
  hits: [],
  sampleOnly: false,
  scanning: false,
};

const STORAGE_KEY = 'boll-scanner:rules';

// ----- 工具 -----
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function fmtPrice(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  if (Math.abs(v) >= 100) return v.toFixed(2);
  if (Math.abs(v) >= 1) return v.toFixed(3);
  return v.toFixed(6);
}

function patternTagClass(pattern) {
  if (pattern === 'cross_mid_up' || pattern === 'upper_breakout' || pattern === 'mid_trend_up') return 'bull';
  if (pattern === 'cross_mid_down' || pattern === 'lower_breakout' || pattern === 'mid_trend_down') return 'bear';
  return 'neutral';
}

function timeNow() {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}

function setLastScanInfo(text) {
  $('#last-scan-info').textContent = text;
}

// ----- 健康检查 + 数据源状态 -----
async function checkHealth() {
  const pill = $('#health-pill');
  try {
    const data = await api.health();
    pill.textContent = '● 已连接';
    pill.className = 'pill ok';
    return data;
  } catch (e) {
    pill.textContent = '● 后端离线';
    pill.className = 'pill err';
    return null;
  }
}

async function refreshProviderStatus() {
  const box = $('#provider-status');
  box.textContent = '正在探测数据源…';
  try {
    const s = await api.status();
    const fmt = (v) => v === 'ok'
      ? '<span style="color:var(--green)">✓ 可用</span>'
      : `<span style="color:var(--red)">✗ 不可用 (${v || 'unknown'})</span>`;
    box.innerHTML = `代理: <code>${s.proxy || '未设置'}</code> · Binance ${fmt(s.binance)} · OKX ${fmt(s.okx)}`;
  } catch (e) {
    box.textContent = '探测失败: ' + e.message;
  }
}

/** 缓存版 (不再轮询, 只在需要时调一次)。 */
let _providerStatusInFlight = null;
async function refreshProviderStatusOnce() {
  if (_providerStatusInFlight) return _providerStatusInFlight;
  _providerStatusInFlight = refreshProviderStatus().finally(() => {
    _providerStatusInFlight = null;
  });
  return _providerStatusInFlight;
}

async function refreshCacheStatus() {
  const box = $('#cache-status');
  box.textContent = '缓存: 检测中…';
  try {
    const s = await api.cacheStats();
    const total = (s.tables || {}).klines || 0;
    const syms = (s.tables || {}).symbols || 0;
    if (total === 0 && syms === 0) {
      box.innerHTML = `缓存: <b>空</b> · 数据仅在扫描时按需写入 · <a href="/cache.html" style="color:var(--primary)">打开管理</a>`;
      return;
    }
    const parts = (s.klines_by_market || []).map((x) => `${x.market}: ${x.rows}行 (${x.symbols}标的 × ${x.intervals}周期)`).join(' · ');
    box.innerHTML = `缓存: <b>${s.db_size_mb} MB</b> · 标的 <b>${syms}</b> · K线 <b>${total}</b> 行${parts ? ' · ' + parts : ''} · <a href="/cache.html" style="color:var(--primary)">打开管理</a> · <a href="#" id="cache-clear-link" style="color:var(--primary)">清空</a>`;
    $('#cache-clear-link').onclick = async (e) => {
      e.preventDefault();
      if (!confirm('确认清空所有缓存? 下次扫描会重新拉取数据。')) return;
      try { await api.cacheClear(); alert('已清空'); refreshCacheStatus(); } catch (err) { alert('失败: ' + err.message); }
    };
  } catch (e) {
    box.textContent = '缓存: 不可用 (' + e.message + ')';
  }
}

// ----- 拉取形态定义 -----
async function loadPatterns() {
  try {
    const data = await api.patterns();
    if (data && data.patterns) {
      filterMgr.setPatterns(data.patterns);
    }
  } catch (e) {
    console.warn('拉取形态失败, 用默认:', e);
  }
}

// ----- 扫描 (SSE 流式) -----
async function runScan(scope = 'all') {
  if (state.scanning) return;
  const rules = filterMgr.getRules();
  if (!rules.length) {
    alert('请先添加至少一条规则');
    return;
  }
  state.scanning = true;
  state.scope = scope;

  // crypto 路径扫描前, 先探测一次数据源 (避免定时轮询)
  if (state.market === 'crypto' || state.market === 'crypto_okx') {
    await refreshProviderStatusOnce();
  }

  const btn = $('#btn-scan');
  const btnSample = $('#btn-scan-sample');
  const btnHs300 = $('#btn-scan-hs300');
  [btn, btnSample, btnHs300].forEach((b) => (b.disabled = true));
  btn.textContent = '扫描中…';

  setProgress(0, '准备中…', 0);
  const t0 = Date.now();

  // 先决定 symbol 列表
  let body = {
    market: state.market,
    rules,
    combine: 'all',
    limit: 100,
    concurrency: 8,
  };

  try {
    if (scope === 'sample') {
      const list = state.market === 'a_share'
        ? (await api.aShareList()).items
        : (await api.cryptoList(state.provider)).items;
      body.symbols = list.slice(0, 50).map((x) => x.symbol);
    } else if (scope === 'hs300') {
      const list = (await api.aShareHs300()).items;
      body.symbols = list.map((x) => x.symbol);
    }
  } catch (e) {
    alert('拉取股票列表失败: ' + e.message);
    state.scanning = false;
    [btn, btnSample, btnHs300].forEach((b) => (b.disabled = false));
    btn.textContent = '🔍 扫描全市场';
    return;
  }

  state.hits = [];
  $('#hit-count').textContent = 0;
  $('#result-tbody').innerHTML = '<tr class="empty"><td colspan="5">扫描中…</td></tr>';

  // 用 SSE 接收实时进度
  await new Promise((resolve) => {
    const url = (window.API_BASE || '') + '/api/scan/stream';
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(async (res) => {
      if (!res.ok || !res.body) {
        const txt = await res.text();
        alert('扫描请求失败: ' + txt);
        return resolve();
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      const handleEvent = (rawEvent, dataStr) => {
        if (!dataStr) return;
        let data;
        try { data = JSON.parse(dataStr); } catch { return; }
        if (rawEvent === 'progress') {
          const live = data.latest_hit ? 1 : 0;
          setProgress(data.percent, `扫描中 · ${data.done} / ${data.total} (${data.percent}%)`, live);
          if (data.latest_hit) {
            state.hits.push({
              symbol: data.latest_hit.symbol,
              name: data.latest_hit.name,
              display: data.latest_hit.display,
              last_close: data.latest_hit.last_close,
              last_mid: data.latest_hit.last_mid,
              rules: data.latest_hit.rules,
            });
            renderResults();
          }
        } else if (rawEvent === 'done') {
          setProgress(100, `完成 · 命中 ${data.hit_count} · 用时 ${data.elapsed_sec}s · 错误 ${data.errors}`, 0);
          setLastScanInfo(`上次扫描 ${timeNow()} · ${state.market} · 命中 ${data.hit_count}`);
        } else if (rawEvent === 'error') {
          alert('扫描出错: ' + data.message);
        }
      };
      const readChunk = () => reader.read().then(({ value, done }) => {
        if (done) return resolve();
        buf += decoder.decode(value, { stream: true });
        // SSE 事件以 \n\n 分隔
        const parts = buf.split('\n\n');
        buf = parts.pop() || '';
        for (const part of parts) {
          let ev = 'message', dataStr = '';
          for (const line of part.split('\n')) {
            if (line.startsWith('event:')) ev = line.slice(6).trim();
            else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
          }
          handleEvent(ev, dataStr);
        }
        readChunk();
      });
      readChunk();
    }).catch((e) => {
      alert('扫描请求异常: ' + e.message);
      resolve();
    });
  });

  [btn, btnSample, btnHs300].forEach((b) => (b.disabled = false));
  btn.textContent = '🔍 扫描全市场';
  state.scanning = false;
}

function setProgress(pct, text, liveHits) {
  const box = $('#progress');
  if (pct === 0 && !text) {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  $('#progress-fill').style.width = pct + '%';
  $('#progress-text').textContent = text;
  const hitsBox = $('#progress-hits');
  if (liveHits !== undefined) {
    const total = state.hits.length;
    hitsBox.textContent = total
      ? `📌 当前累计命中 ${total} 个 (实时更新)`
      : '⏳ 暂无命中';
  }
}

// ----- 结果渲染 -----
function renderResults() {
  const tb = $('#result-tbody');
  const filter = ($('#filter-input').value || '').trim().toUpperCase();
  $('#hit-count').textContent = state.hits.length;

  if (!state.hits.length) {
    tb.innerHTML = `<tr class="empty"><td colspan="5">未命中标的</td></tr>`;
    return;
  }

  const rows = state.hits.filter((h) =>
    !filter || h.symbol.toUpperCase().includes(filter) || (h.name || '').toUpperCase().includes(filter)
  );

  tb.innerHTML = rows.map((h) => {
    const tags = Object.values(h.rules || {}).map((r) =>
      `<span class="tag ${patternTagClass(r.pattern)}" title="${r.detail || ''}">${r.interval} · ${r.pattern_label || r.pattern}</span>`
    ).join('');
    const price = fmtPrice(h.last_close);
    return `
      <tr data-symbol="${h.symbol}" data-market="${state.market}">
        <td><code>${h.symbol}</code></td>
        <td>${h.name || '-'}</td>
        <td>${tags || '-'}</td>
        <td>${price}</td>
        <td><button class="btn small" data-act="view">查看 K 线</button></td>
      </tr>
    `;
  }).join('');

  if (!rows.length) {
    tb.innerHTML = `<tr class="empty"><td colspan="5">没有匹配的标的</td></tr>`;
    return;
  }

  tb.querySelectorAll('button[data-act="view"]').forEach((b) => {
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      const tr = b.closest('tr');
      const symbol = tr.dataset.symbol;
      const hit = state.hits.find((h) => h.symbol === symbol);
      const display = `${hit.name || ''} (${hit.symbol})`;
      const defaultIv = Object.keys(hit.rules || {})[0] || '1d';
      chart.show(state.market, symbol, display, defaultIv, state.provider);
    });
  });
}

// ----- 市场切换 -----
function bindMarketSwitch() {
  $$('.seg-btn').forEach((b) => {
    b.addEventListener('click', () => {
      $$('.seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.market = b.dataset.market;
      state.provider = b.dataset.provider || 'binance';
      state.hits = [];
      renderResults();
    });
  });
}

// ----- 规则持久化 -----
function saveRules() {
  const rules = filterMgr.getRules();
  localStorage.setItem(STORAGE_KEY, JSON.stringify(rules));
  alert('已保存到本地');
}
function loadRules() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const rules = JSON.parse(raw);
    if (Array.isArray(rules) && rules.length) {
      filterMgr.setRules(rules);
    }
  } catch (e) {
    console.warn('加载规则失败:', e);
  }
}

// ----- 启动 -----
let filterMgr, chart;

function init() {
  filterMgr = new FilterManager($('#rule-list'));
  filterMgr.onChange = () => {};

  chart = new KLineChart({
    modal: $('#chart-modal'),
    container: $('#chart-container'),
    titleEl: $('#chart-title'),
    subEl: $('#chart-sub'),
    intervalsEl: $('#chart-intervals'),
    infoEl: $('#chart-boll-info'),
    closeBtn: $('#chart-close'),
    api,
  });

  bindMarketSwitch();
  $('#btn-add-rule').addEventListener('click', () => filterMgr.add());
  $('#btn-scan').addEventListener('click', () => runScan('all'));
  $('#btn-scan-sample').addEventListener('click', () => runScan('sample'));
  $('#btn-scan-hs300').addEventListener('click', () => runScan('hs300'));
  $('#btn-save').addEventListener('click', saveRules);
  $('#btn-load').addEventListener('click', loadRules);
  $('#filter-input').addEventListener('input', renderResults);
  $('#btn-open-settings').addEventListener('click', () => {
    openSettings({
      onChange: () => {
        // 代理/缓存策略变了, 重新探测数据源
        refreshProviderStatus();
        refreshCacheStatus();
      },
    });
  });

  // 默认 1 条规则, 方便用户直接使用
  filterMgr.add({ interval: '15m', pattern: 'cross_mid_up' });
  filterMgr.add({ interval: '1h',  pattern: 'cross_mid_up' });
  filterMgr.add({ interval: '4h',  pattern: 'cross_mid_up' });

  // 启动时尝试加载本地规则
  loadRules();

  // 后端健康
  checkHealth();
  setInterval(checkHealth, 15000);

  // 数据源探测: 仅在首连成功时探测一次, 不再定时轮询
  checkHealth().then((h) => { if (h) refreshProviderStatusOnce(); });

  // 缓存状态
  refreshCacheStatus();
  setInterval(refreshCacheStatus, 30000);

  // 时间
  setInterval(() => { $('#now-time').textContent = timeNow(); }, 1000);
  $('#now-time').textContent = timeNow();

  // SW
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  // 加载形态
  loadPatterns();
}

document.addEventListener('DOMContentLoaded', init);
