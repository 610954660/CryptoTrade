// 扫描复选筛选 UI
// 根据当前 market 拉 /api/scan/filter-options, 渲染 4 个 (A 股) 或 2 个 (Crypto) 复选 group。
// 收集结果: { [groupKey]: Set<optionKey> }
// - 同 group 内多选 = OR
// - 跨 group 多选 = AND
// - 全空 = 不限 (后端视为全市场)
//
// 切换市场时清空。

import { api } from './api.js';

export class ScanFilters {
  constructor({ container, card, hintEl, clearBtnEl, onChange }) {
    this.container = container;
    this.card = card;
    this.hintEl = hintEl;
    this.clearBtnEl = clearBtnEl;
    this.onChange = onChange || (() => {});
    /** @type {Record<string, Set<string>>} */
    this.selected = {};
    this.market = null;
    this._loadInFlight = null;

    if (this.clearBtnEl) {
      this.clearBtnEl.addEventListener('click', () => this.clearAll());
    }
  }

  /** 切换市场时调用, 重新拉维度 + 渲染。 同 market 不重复拉。 */
  async setMarket(market, preselected) {
    const m = market || 'a_share';
    if (m === this.market && this._rendered) {
      // 已是当前市场: 仅应用 preselected (不重渲)
      if (preselected) this.applySelected(preselected);
      this.onChange(this.getFilters());
      return;
    }
    this.market = m;
    this.selected = {};
    await this._loadAndRender();
    // 应用缓存的勾选 (用 applySelected, 不触发 onChange, 避免反复写缓存)
    if (preselected) this.applySelected(preselected);
    // 最后只触发一次 onChange (把最终状态报告给 app.js)
    this.onChange(this.getFilters());
  }

  /** 同步外部状态 (例如从缓存恢复): 直接覆盖 selected 并刷新 UI。
   * 仅保留当前 UI 中真实存在的 group/option 组合, 丢弃陈旧项 (如换市场后旧 group 不存在)。 */
  applySelected(selected) {
    this.selected = {};
    if (!selected || typeof selected !== 'object') return;
    // 收集 UI 里真实存在的 group -> Set(option)
    const uiMap = new Map();
    this.container.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      const g = cb.dataset.group;
      if (!uiMap.has(g)) uiMap.set(g, new Set());
      uiMap.get(g).add(cb.value);
    });
    for (const [gk, opts] of Object.entries(selected)) {
      const uiOpts = uiMap.get(gk);
      if (!uiOpts) continue;  // 此 group 在当前市场不存在 -> 跳过
      const valid = (opts || []).map(String).filter((o) => uiOpts.has(o));
      if (valid.length) {
        this.selected[gk] = new Set(valid);
      }
    }
    // 把对应 checkbox 勾上
    this.container.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      const g = cb.dataset.group;
      cb.checked = !!(this.selected[g] && this.selected[g].has(cb.value));
    });
  }

  async _loadAndRender() {
    if (this._loadInFlight) return this._loadInFlight;
    this._loadInFlight = (async () => {
      try {
        const data = await api.scanFilterOptions(this.market);
        this._render(data);
        this._rendered = true;
      } catch (e) {
        console.warn('拉取筛选维度失败:', e);
        this._renderError(e.message || String(e));
      } finally {
        this._loadInFlight = null;
      }
    })();
    return this._loadInFlight;
  }

  _render(data) {
    this.card.style.display = '';
    this.hintEl.textContent = data.hint || '';
    this.container.innerHTML = '';

    for (const group of (data.groups || [])) {
      const row = document.createElement('div');
      row.className = 'scan-filter-group';
      const label = document.createElement('span');
      label.className = 'scan-filter-label';
      label.textContent = group.label;
      row.appendChild(label);

      const opts = document.createElement('div');
      opts.className = 'scan-filter-options';
      for (const opt of (group.options || [])) {
        const id = `sf_${group.key}_${opt.key}`.replace(/[^a-z0-9_]/gi, '_');
        const wrap = document.createElement('label');
        wrap.className = 'scan-filter-chip';
        wrap.htmlFor = id;
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.id = id;
        cb.value = opt.key;
        cb.dataset.group = group.key;
        cb.addEventListener('change', (e) => this._onToggle(group.key, opt.key, e.target.checked));
        const txt = document.createElement('span');
        txt.textContent = opt.label;
        wrap.appendChild(cb);
        wrap.appendChild(txt);
        opts.appendChild(wrap);
      }
      row.appendChild(opts);
      this.container.appendChild(row);
    }
  }

  _renderError(msg) {
    this.card.style.display = '';
    this.hintEl.textContent = '';
    this.container.innerHTML = `<div class="muted small" style="color:var(--red)">筛选维度加载失败: ${msg}</div>`;
  }

  _onToggle(groupKey, optKey, checked) {
    if (!this.selected[groupKey]) this.selected[groupKey] = new Set();
    if (checked) this.selected[groupKey].add(optKey);
    else this.selected[groupKey].delete(optKey);
    if (this.selected[groupKey].size === 0) delete this.selected[groupKey];
    this.onChange(this.getFilters());
  }

  clearAll() {
    this.selected = {};
    this.container.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.checked = false;
    });
    this.onChange(this.getFilters());
  }

  /** 返回 { [groupKey]: [optionKey, ...] }; 空 group 会被过滤掉。 */
  getFilters() {
    const out = {};
    for (const [k, v] of Object.entries(this.selected)) {
      if (v && v.size) out[k] = Array.from(v);
    }
    return out;
  }

  /** 当前已选数量, 用于 UI 提示。 */
  count() {
    return Object.values(this.selected).reduce((s, set) => s + (set?.size || 0), 0);
  }
}
