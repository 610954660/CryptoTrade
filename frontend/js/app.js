// 应用主入口 - 串起所有 UI 与状态
import { api } from './api.js';
import { ConfigManager } from './config-manager.js';
import { KLineChart } from './chart.js';
import { openSettings } from './settings.js';
import { ScanFilters } from './scan-filters.js';

const state = {
  market: 'crypto_okx',     // 当前选中的市场 (默认 OKX, 大陆可直连)
  provider: 'okx',          // crypto 时的子 provider
  hits: [],
  sampleOnly: false,
  scanning: false,
  /** { [groupKey]: [optionKey, ...] } 来自 ScanFilters, 传给后端做预筛 */
  tagFilters: {},
  /** K 线筛选总开关: false 时跳过 pipeline, 直接把标的预筛池全部输出 */
  klineFilterEnabled: true,
  /** 分页 */
  page: 1,
  pageSize: 100,
};

const STORAGE_KEY = 'boll-scanner:rules';
const UI_CACHE_KEY = 'boll-scanner:ui:v1';

/** UI 状态按市场分缓存: { a_share: {tagFilters, klineFilterEnabled}, crypto: {...}, crypto_okx: {...} } */
function loadUiCache() {
  try {
    const raw = localStorage.getItem(UI_CACHE_KEY);
    if (!raw) return {};
    return JSON.parse(raw) || {};
  } catch { return {}; }
}
function saveUiCache(obj) {
  try { localStorage.setItem(UI_CACHE_KEY, JSON.stringify(obj)); } catch {}
}
function getMarketCache(market) {
  const all = loadUiCache();
  return all[market] || {};
}
function setMarketCache(market, patch) {
  const all = loadUiCache();
  all[market] = { ...(all[market] || {}), ...patch };
  saveUiCache(all);
}

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

// ----- 拉取形态定义 (旧版, 现在由 ConfigManager 内部处理) -----
// 保留为空函数, 兼容早期 init 调用
async function loadPatterns() {
  // no-op: ConfigManager 直接从 /api/indicators 拉
}

// ----- 扫描 (SSE 流式) -----
async function runScan(scope = 'all') {
  if (state.scanning) return;
  const allRules = cfgMgr.getSelectedRules();
  let validRules = [];
  // K 线筛选关闭: 不校验规则, 跳过 pipeline, 直接把标的预筛池全部当命中
  if (!state.klineFilterEnabled) {
    // 即便关闭了 K 线筛选, 也允许空规则: 后端走"无规则"分支, 输出全部标的预筛池
  } else {
    if (!allRules.length) {
      alert('当前配置没有规则, 请先在下方添加。');
      return;
    }
    // 过滤掉未填完整的 (空 interval/pattern/indicator)
    validRules = allRules.filter((r) => r.interval && r.indicator && r.pattern);
    if (!validRules.length) {
      alert('当前配置的规则都没填完整 (周期/指标/形态)。\n请在下拉框里选择具体的值。');
      return;
    }
  }
  // 提示有未保存修改 (但不阻塞扫描 - 用内存中的最新规则跑)
  if (cfgMgr.isDirty()) {
    if (!confirm('当前配置有未保存的修改。\n\n点「确定」使用当前未保存的规则扫描\n点「取消」先保存')) {
      return;
    }
  }
  state.scanning = true;
  state.scope = scope;

  // crypto 路径扫描前, 先探测一次数据源 (避免定时轮询)
  if (state.market === 'crypto' || state.market === 'crypto_okx') {
    await refreshProviderStatusOnce();
  }

  const btn = $('#btn-scan');
  btn.disabled = true;
  btn.textContent = '扫描中…';

  setProgress(0, '准备中…', 0);
  const t0 = Date.now();

  // 先决定 symbol 列表
  let body = {
    market: state.market,
    rules: validRules,  // 过滤掉未填完整的 (K线关闭时这里就是空数组)
    combine: 'all',
    limit: 100,
    concurrency: 8,
    kline_filter_enabled: state.klineFilterEnabled,  // 总开关
  };
  // 复选筛选 (空对象 = 不限, 后端走全市场)
  if (state.tagFilters && Object.keys(state.tagFilters).length) {
    body.tag_filters = state.tagFilters;
  }

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
    [btn].forEach((b) => (b.disabled = false));
    btn.textContent = '🔍 扫描';
    return;
  }

  state.hits = [];
  state.page = 1;
  _renderedSymbols.clear();
  _tbRowCount = 0;
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
            appendNewHits();  // 追加式: 保留按钮焦点 / 监听 / 滚动
          }
        } else if (rawEvent === 'done') {
          setProgress(100, `完成 · 命中 ${data.hit_count} · 用时 ${data.elapsed_sec}s · 错误 ${data.errors}`, 0);
          setLastScanInfo(`上次扫描 ${timeNow()} · ${state.market} · 命中 ${data.hit_count}`);
          // K线筛选关闭时, progress 事件没有携带 hit; 后端把所有 hit 放在 done 事件里补齐
          if (data.hits && data.hits.length && state.hits.length === 0) {
            state.hits = data.hits;
            renderResults();
          }
          // 扫描完成: 缓存有新行, 刷新一次缓存状态 (不再定时)
          refreshCacheStatus();
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

  [btn].forEach((b) => (b.disabled = false));
  btn.textContent = '🔍 扫描';
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
/** 已渲染的 hit symbol 集合 (避免重复 append)。 */
const _renderedSymbols = new Set();
/** 当前 tbody 中实际可见的 row 数量 (实时刷新统计用)。 */
let _tbRowCount = 0;

function rowHtml(h) {
  const tags = Object.values(h.rules || {}).map((r) =>
    `<span class="tag ${patternTagClass(r.pattern)}" title="${(r.detail || '').replace(/"/g, '&quot;')}">${r.interval} · ${r.pattern_label || r.pattern}</span>`
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
}

/** 全量重渲 (用于分页切换 / 过滤变化 / 扫描开始清空)。 会重建 tbody, 重建后 _renderedSymbols 重新填充。 */
function renderResults() {
  const tb = $('#result-tbody');
  const filter = ($('#filter-input').value || '').trim().toUpperCase();
  $('#hit-count').textContent = state.hits.length;

  const filtered = state.hits.filter((h) =>
    !filter || h.symbol.toUpperCase().includes(filter) || (h.name || '').toUpperCase().includes(filter)
  );

  const totalPages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
  if (state.page > totalPages) state.page = totalPages;
  if (state.page < 1) state.page = 1;
  const start = (state.page - 1) * state.pageSize;
  const pageRows = filtered.slice(start, start + state.pageSize);

  const pager = $('#result-pager');
  if (pager) {
    pager.style.display = filtered.length ? '' : 'none';
    $('#pager-info').textContent = `第 ${state.page} / ${totalPages} 页 · 共 ${filtered.length} 条`;
    $('#pager-prev').disabled = state.page <= 1;
    $('#pager-next').disabled = state.page >= totalPages;
  }

  if (!state.hits.length) {
    tb.innerHTML = `<tr class="empty"><td colspan="5">未命中标的</td></tr>`;
    _renderedSymbols.clear();
    _tbRowCount = 0;
    return;
  }

  if (!pageRows.length) {
    tb.innerHTML = `<tr class="empty"><td colspan="5">没有匹配的标的</td></tr>`;
    _renderedSymbols.clear();
    _tbRowCount = 0;
    return;
  }

  tb.innerHTML = pageRows.map(rowHtml).join('');
  _renderedSymbols.clear();
  for (const h of pageRows) _renderedSymbols.add(h.symbol);
  _tbRowCount = pageRows.length;
}

/** 追加式渲染: 只把 state.hits 里新增的 symbol 追加到 tbody 末尾。 不动已渲染的行。
 *  这样 SSE 实时推 hit 时不会重建 DOM, 按钮焦点 / 监听 / 滚动位置全保留。
 *  同时更新顶部命中计数 + 进度文本。 */
function appendNewHits() {
  if (!state.hits.length) return;
  const tb = $('#result-tbody');
  const newOnes = state.hits.filter((h) => !_renderedSymbols.has(h.symbol));
  if (!newOnes.length) {
    $('#hit-count').textContent = state.hits.length;
    return;
  }
  // 用 DocumentFragment 一次性插入, 减少 layout
  const frag = document.createDocumentFragment();
  for (const h of newOnes) {
    const wrap = document.createElement('tbody'); // 临时容器解析 HTML
    wrap.innerHTML = rowHtml(h).trim();
    const tr = wrap.firstElementChild;
    if (tr) {
      frag.appendChild(tr);
      _renderedSymbols.add(h.symbol);
    }
  }
  // 替换"扫描中…"占位行 (如果有)
  if (_tbRowCount === 0) {
    const empty = tb.querySelector('tr.empty');
    if (empty) empty.remove();
  }
  tb.appendChild(frag);
  _tbRowCount = tb.querySelectorAll('tr').length;
  $('#hit-count').textContent = state.hits.length;
}

// ----- 市场切换 -----
function bindMarketSwitch() {
  $$('.seg-btn').forEach((b) => {
    b.addEventListener('click', () => {
      const newMarket = b.dataset.market;
      const newProvider = b.dataset.provider || 'binance';
      // 1) 缓存当前市场的 UI 状态 (切换前先存)
      if (state.market) {
        setMarketCache(state.market, {
          tagFilters: state.tagFilters || {},
          klineFilterEnabled: state.klineFilterEnabled,
        });
      }
      // 2) 切换 active
      $$('.seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.market = newMarket;
      state.provider = newProvider;
      state.hits = [];
      state.page = 1;
      // 3) 从缓存恢复新市场的 UI 状态
      const cached = getMarketCache(newMarket);
      const cachedFilters = cached.tagFilters || {};
      const cachedEnabled = cached.klineFilterEnabled !== false;
      state.klineFilterEnabled = cachedEnabled;
      // 通知 ConfigManager 按市场重拉指标 + 配置
      if (cfgMgr) cfgMgr.setMarket(state.market);
      // 通知 ScanFilters 按市场重渲染复选, 传缓存的勾选
      if (scanFilters) scanFilters.setMarket(state.market, cachedFilters);
      // 应用 master 开关状态
      const masterCb = $('#rule-master-enabled');
      if (masterCb) {
        masterCb.checked = cachedEnabled;
        masterCb.dispatchEvent(new Event('change'));
      }
      // 同步 state.tagFilters 供下次 runScan 用
      state.tagFilters = cachedFilters;
      // 4) 记下最后选中的市场 (现在 state.market 已是新值)
      const all = loadUiCache();
      all._lastMarket = state.market;
      saveUiCache(all);
      renderResults();
    });
  });
}

// ----- 启动 -----
let cfgMgr, chart, scanFilters;

async function init() {
  // 优先从缓存恢复选中市场 (用户上次停在哪就停在哪)
  try {
    const ui = loadUiCache();
    if (ui._lastMarket && ['crypto', 'crypto_okx', 'a_share'].includes(ui._lastMarket)) {
      state.market = ui._lastMarket;
      state.provider = ui._lastMarket === 'crypto' ? 'binance' : (ui._lastMarket === 'crypto_okx' ? 'okx' : 'a_share');
    }
  } catch {}
  // 把对应按钮设为 active
  $$('.seg-btn').forEach((b) => {
    if (b.dataset.market === state.market) b.classList.add('active');
    else b.classList.remove('active');
  });
  // 配置 + 规则管理 (多配置 + 持久化在后端)
  cfgMgr = new ConfigManager({
    tabsEl: $('#config-tabs'),
    actionsEl: $('#config-actions'),
    listEl: $('#rule-list'),
    addRuleEl: $('#add-rule-btn'),
    onRulesChange: (rules) => {
      // 规则变化时可在这里做实时提示, 当前不需要
    },
  });
  cfgMgr.setMarket(state.market);  // 先设 market, 再 load (load 内部也会 refresh)
  await cfgMgr.load();

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

  // 扫描复选筛选
  scanFilters = new ScanFilters({
    container: $('#scan-filters'),
    card: $('#scan-filters-card'),
    hintEl: $('#scan-filters-hint'),
    clearBtnEl: $('#scan-filters-clear'),
    onChange: (filters) => {
      state.tagFilters = filters;
      // 缓存当前市场的 tagFilters
      setMarketCache(state.market, { tagFilters: filters });
      // 顶部"🔍 扫描"按钮旁显示已选数
      const n = Object.values(filters).reduce((s, arr) => s + arr.length, 0);
      const scanBtn = $('#btn-scan');
      if (scanBtn) scanBtn.textContent = n ? `🔍 扫描 (已筛 ${n} 项)` : '🔍 扫描';
    },
  });
  // 初始化时, 把当前市场的缓存复选应用上
  const initCached = getMarketCache(state.market);
  state.tagFilters = initCached.tagFilters || {};
  state.klineFilterEnabled = initCached.klineFilterEnabled !== false;
  // 设置 master 开关初值 (change handler 会读 cb.checked)
  const _initMasterCb = $('#rule-master-enabled');
  if (_initMasterCb) _initMasterCb.checked = state.klineFilterEnabled;
  await scanFilters.setMarket(state.market, state.tagFilters);

  // 事件委托: 一次绑 #result-tbody 的 click, 处理所有 [data-act="view"] 按钮
  // 避免每次 renderResults() 重建 DOM 时丢失按钮事件
  $('#result-tbody').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act="view"]');
    if (!btn) return;
    e.stopPropagation();
    const tr = btn.closest('tr');
    if (!tr) return;
    const symbol = tr.dataset.symbol;
    const hit = state.hits.find((h) => h.symbol === symbol);
    if (!hit) return;
    const display = `${hit.name || ''} (${hit.symbol})`;
    const firstRule = Object.values(hit.rules || {})[0];
    const defaultIv = (firstRule && firstRule.interval) || '1d';
    chart.show(state.market, symbol, display, defaultIv, state.provider);
  });

  $('#btn-scan').addEventListener('click', () => runScan('all'));
  $('#filter-input').addEventListener('input', () => {
    state.page = 1;  // 过滤变化 -> 重置到第 1 页
    renderResults();
  });

  // 分页
  $('#pager-prev').addEventListener('click', () => {
    if (state.page > 1) { state.page--; renderResults(); }
  });
  $('#pager-next').addEventListener('click', () => {
    state.page++; renderResults();
  });
  $('#pager-page-size').addEventListener('change', (e) => {
    state.pageSize = parseInt(e.target.value, 10) || 100;
    state.page = 1;
    renderResults();
  });

  // K 线筛选总开关: 控制是否走 pipeline
  const masterCb = $('#rule-master-enabled');
  const rulesCard = $('#rules-card');
  function applyMasterSwitch() {
    state.klineFilterEnabled = !!masterCb.checked;
    if (rulesCard) rulesCard.classList.toggle('rules-card-disabled', !state.klineFilterEnabled);
  }
  masterCb.addEventListener('change', applyMasterSwitch);
  masterCb.addEventListener('change', () => {
    // 缓存到当前市场
    setMarketCache(state.market, { klineFilterEnabled: !!masterCb.checked });
  });
  applyMasterSwitch();
  $('#btn-open-settings').addEventListener('click', () => {
    openSettings({
      onChange: () => {
        // 代理/缓存策略变了, 重新探测数据源
        refreshProviderStatus();
        refreshCacheStatus();
      },
    });
  });
  // 后端健康
  checkHealth();
  setInterval(checkHealth, 15000);

  // 数据源探测: 仅在首连成功时探测一次, 不再定时轮询
  checkHealth().then((h) => { if (h) { refreshProviderStatusOnce(); refreshCacheStatus(); } });

  // 缓存状态: 不再定时刷新, 仅在 (a) 首连成功 (b) 扫描完成 时拉一次。
  // scan/done 事件里调用 _cacheStatusAfterScan() 即可。

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
