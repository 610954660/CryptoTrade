// 缓存管理页面逻辑
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const API = (window.API_BASE || '') + '/api';

const state = {
  market: '',
  search: '',
  items: [],        // 全部标的 (服务端一次拉全)
  filtered: [],     // 经过搜索过滤后的
  selected: new Set(),
  page: 1,
  pageSize: 100,
};

async function _fetch(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).detail || ''; } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

const api = {
  stats: () => _fetch('/cache/stats'),
  symbols: (market = '') => _fetch('/cache/symbols' + (market ? `?market=${market}` : '')),
  clear: (market = '') => _fetch('/cache/clear' + (market ? `?market=${market}` : ''), { method: 'POST' }),
  deleteSymbols: (market, symbols) => _fetch('/cache/delete-symbols', { method: 'POST', body: JSON.stringify({ market, symbols }) }),
  deleteKlines: (market, symbol, interval) => _fetch('/cache/delete-klines', { method: 'POST', body: JSON.stringify({ market, symbol, interval }) }),
  refreshSymbol: (market, symbol, interval) => _fetch('/cache/refresh', { method: 'POST', body: JSON.stringify({ market, symbol, interval, limit: 200 }) }),
  health: () => _fetch('/health'),
};

// --------- 确认弹窗 ---------
function confirmDialog(title, text) {
  return new Promise((resolve) => {
    $('#confirm-title').textContent = title;
    $('#confirm-text').textContent = text;
    $('#confirm-modal').classList.remove('hidden');
    const ok = $('#confirm-ok'), cancel = $('#confirm-cancel');
    const onOk = () => { cleanup(); resolve(true); };
    const onCancel = () => { cleanup(); resolve(false); };
    const cleanup = () => {
      $('#confirm-modal').classList.add('hidden');
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
    };
    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
  });
}

// --------- 健康检查 ---------
async function checkHealth() {
  const pill = $('#health-pill');
  try {
    await api.health();
    pill.textContent = '● 已连接';
    pill.className = 'pill ok';
  } catch {
    pill.textContent = '● 后端离线';
    pill.className = 'pill err';
  }
}

// --------- 渲染统计 ---------
async function renderStats() {
  try {
    const s = await api.stats();
    $('#stat-db-size').textContent = `${s.db_size_mb || 0} MB`;
    $('#stat-symbols').textContent = s.tables?.symbols || 0;
    $('#stat-klines').textContent = (s.tables?.klines || 0).toLocaleString();
    $('#stat-markets').textContent = (s.klines_by_market || []).length;
    const parts = (s.klines_by_market || []).map(
      (x) => `${x.market}: ${x.rows.toLocaleString()} 行 (${x.symbols} 标的 × ${x.intervals} 周期)`
    );
    $('#stats-by-market').textContent = parts.join(' · ') || '空';
  } catch (e) {
    $('#stats-by-market').textContent = '统计失败: ' + e.message;
  }
}

// --------- 渲染标的列表 ---------
async function renderSymbols() {
  const tb = $('#tbody');

  // 1) 先按搜索过滤
  const search = state.search.toLowerCase();
  const filtered = state.items.filter((it) =>
    !search ||
    it.symbol.toLowerCase().includes(search) ||
    (it.name || '').toLowerCase().includes(search) ||
    (it.code || '').toLowerCase().includes(search) ||
    (it.display || '').toLowerCase().includes(search)
  );
  state.filtered = filtered;

  // 2) 分页
  const total = filtered.length;
  const pageSize = state.pageSize;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (state.page > totalPages) state.page = totalPages;
  if (state.page < 1) state.page = 1;
  const start = (state.page - 1) * pageSize;
  const end = Math.min(start + pageSize, total);
  const items = filtered.slice(start, end);

  // 3) 分页控件状态
  renderPager(total, totalPages, start, end);

  // 4) 渲染当前页
  if (!total) {
    tb.innerHTML = `<tr class="empty"><td colspan="8">
      暂无缓存数据。<br>
      <span style="color:var(--muted);font-size:12px">去 <a href="/" style="color:var(--primary)">筛选器</a> 跑一次扫描, 数据会按需写入这里。</span>
    </td></tr>`;
    return;
  }
  tb.innerHTML = items.map((it) => {
    const checked = state.selected.has(it.symbol) ? 'checked' : '';
    const lastKline = it.last_kline_time
      ? new Date(it.last_kline_time * 1000).toLocaleString('zh-CN', { hour12: false })
      : '-';
    return `
      <tr data-symbol="${it.symbol}" data-market="${it.market}" class="${checked ? 'row-selected' : ''}">
        <td><input type="checkbox" class="row-check" data-symbol="${it.symbol}" ${checked} /></td>
        <td><code>${it.market}</code></td>
        <td><code>${it.symbol}</code></td>
        <td>${it.name || '-'}</td>
        <td>${(it.kline_count || 0).toLocaleString()}</td>
        <td>${it.intervals_count || 0}</td>
        <td class="muted small">${lastKline}</td>
        <td>
          <button class="action-link" data-act="detail">详情</button>
          <button class="action-link danger" data-act="delete">删除</button>
        </td>
      </tr>
    `;
  }).join('');

  // 绑定行内按钮
  tb.querySelectorAll('input.row-check').forEach((cb) => {
    cb.addEventListener('change', (e) => {
      const sym = e.target.dataset.symbol;
      const tr = cb.closest('tr');
      if (e.target.checked) { state.selected.add(sym); tr.classList.add('row-selected'); }
      else { state.selected.delete(sym); tr.classList.remove('row-selected'); }
      updateSelectionUI();
    });
  });
  tb.querySelectorAll('button[data-act="detail"]').forEach((b) => {
    b.addEventListener('click', () => {
      const tr = b.closest('tr');
      const it = state.items.find((x) => x.symbol === tr.dataset.symbol);
      showDetail(it);
    });
  });
  tb.querySelectorAll('button[data-act="delete"]').forEach((b) => {
    b.addEventListener('click', async () => {
      const tr = b.closest('tr');
      const it = state.items.find((x) => x.symbol === tr.dataset.symbol);
      if (!await confirmDialog('删除标的', `确认从缓存删除 ${it.display}? 会同时清空它的 K 线。`)) return;
      try {
        await api.deleteSymbols(it.market, [it.symbol]);
        state.selected.delete(it.symbol);
        await loadAll();
      } catch (e) { alert('删除失败: ' + e.message); }
    });
  });
}

function updateSelectionUI() {
  const n = state.selected.size;
  $('#sel-count').textContent = n;
  $('#btn-delete-selected').disabled = n === 0;
  $('#btn-refresh-selected').disabled = n === 0;
  const allCheck = $('#check-all');
  if (allCheck) allCheck.checked = n > 0 && n === state.filtered.length;
}

// --------- 分页控件 ---------
function renderPager(total, totalPages, start, end) {
  $('#pager-range').textContent = total === 0 ? '0-0' : `${start + 1}-${end}`;
  $('#pager-total').textContent = total.toLocaleString();
  $('#pager-current').textContent = state.page;
  $('#pager-count').textContent = totalPages;
  // 翻页按钮 disabled
  const first = total > 0 && state.page > 1;
  const last  = total > 0 && state.page < totalPages;
  $('#pager-first').disabled = !first;
  $('#pager-prev').disabled  = !first;
  $('#pager-next').disabled  = !last;
  $('#pager-last').disabled  = !last;
}

function goToPage(p) {
  const totalPages = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
  if (p < 1) p = 1;
  if (p > totalPages) p = totalPages;
  if (p === state.page) return;
  state.page = p;
  renderSymbols();
  // 滚到表格顶部
  const tw = document.querySelector('.table-wrap');
  if (tw) tw.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// --------- 详情弹窗 ---------
function showDetail(it) {
  $('#detail-title').textContent = it.display;
  $('#detail-sub').textContent = `市场 ${it.market} · 缓存更新 ${new Date(it.last_updated * 1000).toLocaleString('zh-CN', { hour12: false })}`;
  const html = `
    <div class="kv-list">
      <div class="kv"><div class="k">市场</div><div class="v">${it.market}</div></div>
      <div class="kv"><div class="k">代码</div><div class="v">${it.symbol}</div></div>
      <div class="kv"><div class="k">A 股 code</div><div class="v">${it.code || '-'}</div></div>
      <div class="kv"><div class="k">名称</div><div class="v">${it.name || '-'}</div></div>
      <div class="kv"><div class="k">K 线总数</div><div class="v">${(it.kline_count || 0).toLocaleString()}</div></div>
      <div class="kv"><div class="k">周期数</div><div class="v">${it.intervals_count || 0}</div></div>
      <div class="kv"><div class="k">最新 K 线</div><div class="v">${it.last_kline_time ? new Date(it.last_kline_time * 1000).toLocaleString('zh-CN', { hour12: false }) : '-'}</div></div>
    </div>
    <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn ghost small" data-act="refresh-1d">刷新 1d K 线</button>
      <button class="btn ghost small" data-act="refresh-1h">刷新 1h K 线</button>
      <button class="btn danger" data-act="delete-klines">清空 K 线</button>
    </div>
  `;
  $('#detail-content').innerHTML = html;
  $('#detail-modal').classList.remove('hidden');
  $('#detail-content button[data-act="refresh-1d"]').onclick = () => refreshOne(it, '1d');
  $('#detail-content button[data-act="refresh-1h"]').onclick = () => refreshOne(it, '1h');
  $('#detail-content button[data-act="delete-klines"]').onclick = async () => {
    if (!await confirmDialog('清空 K 线', `清空 ${it.display} 的全部 K 线缓存?`)) return;
    try {
      await api.deleteKlines(it.market, it.symbol, null);
      $('#detail-modal').classList.add('hidden');
      await loadAll();
    } catch (e) { alert('失败: ' + e.message); }
  };
}

async function refreshOne(it, interval) {
  $('#detail-content button').forEach((b) => b.disabled = true);
  try {
    const r = await api.refreshSymbol(it.market, it.symbol, interval);
    alert(`刷新完成: ${r.rows} 根 K 线`);
    $('#detail-modal').classList.add('hidden');
    await loadAll();
  } catch (e) {
    alert('刷新失败: ' + e.message);
  } finally {
    $('#detail-content button').forEach((b) => b.disabled = false);
  }
}

$('#detail-close').addEventListener('click', () => $('#detail-modal').classList.add('hidden'));
$('#detail-modal').addEventListener('click', (e) => { if (e.target === $('#detail-modal')) $('#detail-modal').classList.add('hidden'); });

// --------- 全部载入 ---------
async function loadAll() {
  try {
    state.items = (await api.symbols(state.market)).items;
    await renderSymbols();
    await renderStats();
    updateSelectionUI();
  } catch (e) {
    $('#tbody').innerHTML = `<tr class="empty"><td colspan="8">加载失败: ${e.message}</td></tr>`;
  }
}

// --------- 事件绑定 ---------
function bind() {
  // 市场 tab
  $$('#market-tabs .seg-btn').forEach((b) => {
    b.addEventListener('click', () => {
      $$('#market-tabs .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.market = b.dataset.market;
      state.selected.clear();
      loadAll();
    });
  });
  // 搜索
  $('#search-input').addEventListener('input', (e) => { state.search = e.target.value; renderSymbols(); });
  // 全选 / 取消 (全选只对当前页生效, 跟多数表格习惯一致)
  $('#check-all').addEventListener('change', (e) => {
    const tb = $('#tbody');
    tb.querySelectorAll('input.row-check').forEach((cb) => {
      cb.checked = e.target.checked;
      const sym = cb.dataset.symbol;
      if (e.target.checked) state.selected.add(sym);
      else state.selected.delete(sym);
    });
    updateSelectionUI();
  });
  $('#btn-select-all').addEventListener('click', () => {
    state.filtered.forEach((it) => state.selected.add(it.symbol));
    renderSymbols();
  });
  $('#btn-select-none').addEventListener('click', () => { state.selected.clear(); renderSymbols(); });

  // 分页
  $('#pager-first').addEventListener('click', () => goToPage(1));
  $('#pager-prev').addEventListener('click', () => goToPage(state.page - 1));
  $('#pager-next').addEventListener('click', () => goToPage(state.page + 1));
  $('#pager-last').addEventListener('click', () => {
    const tp = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
    goToPage(tp);
  });
  $('#pager-size-sel').addEventListener('change', (e) => {
    state.pageSize = parseInt(e.target.value, 10) || 100;
    state.page = 1;
    renderSymbols();
  });
  // 删除选中
  $('#btn-delete-selected').addEventListener('click', async () => {
    if (!state.selected.size) return;
    if (!await confirmDialog('批量删除', `确认删除选中的 ${state.selected.size} 个标的? (会同时清空它们的 K 线)`)) return;
    // 按 market 分组
    const byMarket = {};
    state.selected.forEach((sym) => {
      const it = state.items.find((x) => x.symbol === sym);
      if (it) (byMarket[it.market] = byMarket[it.market] || []).push(sym);
    });
    try {
      for (const [m, syms] of Object.entries(byMarket)) {
        await api.deleteSymbols(m, syms);
      }
      state.selected.clear();
      await loadAll();
    } catch (e) { alert('删除失败: ' + e.message); }
  });
  // 刷新选中 (1d 周期)
  $('#btn-refresh-selected').addEventListener('click', async () => {
    if (!state.selected.size) return;
    if (!await confirmDialog('刷新 K 线', `强制刷新选中 ${state.selected.size} 个标的的 1d K 线?`)) return;
    const list = state.items.filter((it) => state.selected.has(it.symbol));
    let ok = 0, fail = 0;
    for (const it of list) {
      try { await api.refreshSymbol(it.market, it.symbol, '1d'); ok++; }
      catch { fail++; }
    }
    alert(`完成: 成功 ${ok}, 失败 ${fail}`);
    state.selected.clear();
    await loadAll();
  });
  // 一键清空
  $('#btn-clear-all').addEventListener('click', async () => {
    if (!await confirmDialog('⚠️ 清空全部', '确认清空全部缓存? 所有 K 线都会丢失, 下次扫描会重新拉取。')) return;
    try {
      await api.clear('');
      state.selected.clear();
      await loadAll();
    } catch (e) { alert('清空失败: ' + e.message); }
  });
  // 刷新统计
  $('#btn-refresh-stats').addEventListener('click', renderStats);
}

checkHealth();
setInterval(checkHealth, 15000);
bind();
loadAll();
