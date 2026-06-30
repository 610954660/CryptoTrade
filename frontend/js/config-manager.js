// 配置 + 规则 UI 管理
// 一个配置 = 一组规则; 多个配置之间切换, 每个配置可独立保存/删除
//
// 单条规则 schema (新):
//   {
//     interval:    '1d',          // 周期 (按数据源筛选)
//     indicator:   'boll',        // 指标: boll/ma/macd/rsi/kdj/vol/price
//     pattern:     'cross_mid_up',// 形态 (按指标动态列)
//     value:       null,          // 数值 (vol/price/rsi/kdj 阈值时填)
//     lookback:    200,           // 查询 K 线数 (回看窗口)
//     match_count: 1,             // 窗口内需匹配根数
//   }
//
// 向后兼容: 旧 {interval, pattern} 也允许 (indicator 默认 'boll')。

import { api } from './api.js';

const $ = (s, r = document) => r.querySelector(s);

const DEFAULT_RULE = () => ({
  interval: '',
  indicator: '',
  pattern: '',
  value: null,
  lookback: 200,
  match_count: 1,
});

const EMPTY_INDICATOR_PLACEHOLDER = { key: '', label: '— 请选择指标 —', patterns: [] };

// 多个 UI market (crypto / crypto_okx / crypto_binance) -> 一个存储 market (crypto)。
// 配置按"存储 market" 隔离, 但 data provider 仍然按 UI market 区分。
function normalizeConfigMarket(uiMarket) {
  if (uiMarket && uiMarket.startsWith('crypto')) return 'crypto';
  return uiMarket || 'a_share';
}

/**
 * ConfigManager
 * ------------
 * 管多个规则配置, 持久化在后端 (backend/data/configs.json)。
 *
 * DOM 结构:
 *   .config-tabs          页签栏
 *     .cfg-tab            单个页签
 *     .cfg-tab-add        "+" 新建按钮
 *   .config-actions       右侧按钮区
 *     .cfg-save           保存 (仅 dirty 时显示)
 *     .cfg-del            删除
 *   .rule-list            当前配置的规则列表 (5 列: 周期|指标|形态|查询K线|符合K线)
 *   .add-rule-btn         底部"添加规则"长条
 */
export class ConfigManager {
  constructor(opts) {
    this.tabsEl = opts.tabsEl;
    this.actionsEl = opts.actionsEl;
    this.listEl = opts.listEl;
    this.addRuleEl = opts.addRuleEl;
    this.onRulesChange = opts.onRulesChange || (() => {});

    this.configs = [];
    this.selectedId = null;

    /** 指标注册表 (从 /api/indicators 拉): [{key, label, patterns:[{key,label,value_required,value_label}]}] */
    this.indicators = [EMPTY_INDICATOR_PLACEHOLDER];

    /** 当前市场支持的周期: ['5m','15m',...] */
    this.intervals = [];

    /** 当前 market key, 用于按市场过滤指标暴露的形态 + 重拉 indicators。 */
    this.market = 'a_share';

    /** 兼容旧 /api/patterns (BOLL only) */
    this._legacyPatterns = [];

    this.dirty = false;
    this._suspendDirty = false;
    this._loadInFlight = null;

    this.addRuleEl.addEventListener('click', () => this.addRule());
  }

  /** 由 app.js 调用: 当市场切换时重新拉 indicators + 重新拉对应市场的配置。 */
  async setMarket(market) {
    const newMarket = normalizeConfigMarket(market);
    if (newMarket === this.market && this.configs.length) {
      // 同存储市场且已有配置: 只刷新 indicators
      await this.refreshIndicators();
      return;
    }
    if (this.dirty) {
      const ok = confirm('当前配置有未保存的修改, 切换市场会丢失。\n\n点「确定」放弃修改并切换\n点「取消」留在当前市场');
      if (!ok) return;
    }
    this.market = newMarket;
    this.dirty = false;
    this._loadInFlight = null;
    await this.load();        // 重新拉该 market 的配置
    this.onRulesChange(this.getSelectedRules());
  }

  async refreshIndicators() {
    try {
      const data = await api.indicators(this.market);
      this.indicators = [EMPTY_INDICATOR_PLACEHOLDER, ...(data.indicators || [])];
      this.intervals = data.intervals || [];
      this._legacyPatterns = [];  // 不再需要
    } catch (e) {
      console.warn('拉取指标定义失败, 退到本地默认值:', e);
      this.indicators = [EMPTY_INDICATOR_PLACEHOLDER];
      this.intervals = [];
    }
    this.renderRules();
  }

  /** 从后端拉当前市场的全部配置 + 选中。 失败时回退到内存默认。 */
  async load() {
    if (this._loadInFlight) return this._loadInFlight;
    this._loadInFlight = (async () => {
      try {
        const data = await api.configsList(this.market);
        this.configs = data.configs || [];
        this.selectedId = data.selected_id || (this.configs[0] && this.configs[0].id);
        if (!this.configs.length) {
          // 后端应该已经为该 market 兜底建一个默认配置, 但万一没有, 我们也建一个
          const c = await api.configsCreate({
            name: '默认配置',
            market: this.market,
            rules: [DEFAULT_RULE()],
          });
          this.configs = [c];
          this.selectedId = c.id;
        } else {
          // 拿到的是另一个市场的配置? 直接丢弃, 用本市场的
          const allMine = this.configs.every((c) => (c.market || 'a_share') === this.market);
          if (!allMine) {
            this.configs = this.configs.filter((c) => (c.market || 'a_share') === this.market);
            if (!this.configs.length) {
              const c = await api.configsCreate({
                name: '默认配置',
                market: this.market,
                rules: [DEFAULT_RULE()],
              });
              this.configs = [c];
              this.selectedId = c.id;
            } else {
              this.selectedId = this.configs[0].id;
            }
          }
          // 确保每条规则都有完整字段 (向后兼容老数据)
          for (const c of this.configs) {
            c.rules = (c.rules || []).map((r) => ({
              interval: r.interval || '',
              indicator: r.indicator || 'boll',
              pattern: r.pattern || '',
              value: r.value ?? null,
              lookback: r.lookback || 200,
              match_count: r.match_count || 1,
            }));
          }
        }
      } catch (e) {
        console.warn('加载配置失败, 用本地空状态:', e);
        this.configs = [];
        this.selectedId = null;
      } finally {
        this.dirty = false;
        this._suspendDirty = true;
        await this.refreshIndicators();
        this.renderAll();
        this._suspendDirty = false;
        this._loadInFlight = null;
      }
    })();
    return this._loadInFlight;
  }

  // ===== 查询 =====
  getSelected() {
    return this.configs.find((c) => c.id === this.selectedId) || null;
  }
  /** 当前选中配置的规则 (浅拷贝, 不含空规则)。 */
  getSelectedRules() {
    const c = this.getSelected();
    if (!c) return [];
    return c.rules.map((r) => ({ ...r }));
  }
  isDirty() { return this.dirty; }

  // ===== 规则操作 =====
  addRule() {
    const c = this.getSelected();
    if (!c) return;
    c.rules.push(DEFAULT_RULE());
    this._markDirty();
    this.renderRules();
  }

  removeRule(i) {
    const c = this.getSelected();
    if (!c) return;
    c.rules.splice(i, 1);
    this._markDirty();
    this.renderRules();
  }

  setRule(i, patch) {
    const c = this.getSelected();
    if (!c || !c.rules[i]) return;
    const old = c.rules[i];
    c.rules[i] = { ...old, ...patch };
    // 切换 indicator 时清掉 pattern + value
    if (patch.indicator !== undefined && patch.indicator !== old.indicator) {
      c.rules[i].pattern = '';
      c.rules[i].value = null;
    }
    // 切换 pattern 时, 应用形态的默认 lookback/match_count/value, 并清掉 value (如果新 pattern 不需要)
    if (patch.pattern !== undefined) {
      const ind = (this.indicators || []).find((x) => x.key === c.rules[i].indicator);
      const pat = ind && ind.patterns ? ind.patterns.find((p) => p.key === patch.pattern) : null;
      if (pat) {
        if (pat.lookback_min && c.rules[i].lookback < pat.lookback_min) {
          c.rules[i].lookback = pat.lookback_min;
        }
        if (pat.match_count_default && (!c.rules[i].match_count || c.rules[i].match_count < 1)) {
          c.rules[i].match_count = pat.match_count_default;
        }
        if (pat.value_required) {
          if (pat.value_default != null && (c.rules[i].value == null || c.rules[i].value === '')) {
            c.rules[i].value = pat.value_default;
          }
          if (pat.value_min != null && c.rules[i].value != null && c.rules[i].value < pat.value_min) {
            c.rules[i].value = pat.value_min;
          }
        } else {
          c.rules[i].value = null;
        }
      } else {
        c.rules[i].value = null;
      }
    }
    this._markDirty();
    // 数字字段 (lookback/match_count/value) 不重新渲染 (避免 input 失焦)
    // 只有影响 select/visible 字段 (interval, indicator, pattern) 才需要重渲
    const renderAffecting = ['interval', 'indicator', 'pattern'];
    const needsRender = Object.keys(patch).some((k) => renderAffecting.includes(k));
    if (needsRender) {
      this.renderRules();
    } else {
      // 只更新 tab 标题上的 dirty 标记
      this.renderTabs();
      this.renderActions();
    }
  }

  // ===== 配置操作 =====
  async selectConfig(cid, { skipConfirm = false } = {}) {
    if (cid === this.selectedId) return;
    if (this.dirty && !skipConfirm) {
      const ok = confirm('当前配置有未保存的修改, 切换会丢失。\n\n点「确定」放弃修改并切换\n点「取消」留在当前页签');
      if (!ok) return;
    }
    // 防御: 切到不是当前市场的配置时拒绝
    const target = this.configs.find((c) => c.id === cid);
    if (target && (target.market || 'a_share') !== this.market) {
      alert('该配置属于其他市场, 不能切换。');
      return;
    }
    this.selectedId = cid;
    this.dirty = false;
    this._suspendDirty = true;
    this.renderAll();
    this._suspendDirty = false;
    try { await api.configsSelect(cid); } catch (e) { console.warn('同步选中状态失败:', e); }
    this.onRulesChange(this.getSelectedRules());
  }

  async createConfig() {
    const used = new Set(this.configs.map((c) => c.name));
    let n = this.configs.length + 1;
    let name = `配置 ${n}`;
    while (used.has(name)) { n++; name = `配置 ${n}`; }
    try {
      const c = await api.configsCreate({ name, market: this.market, rules: [DEFAULT_RULE()] });
      this.configs.push(c);
      this.selectedId = c.id;
      this.dirty = false;
      this._suspendDirty = true;
      this.renderAll();
      this._suspendDirty = false;
      this.onRulesChange(this.getSelectedRules());
      this.startRename(c.id);
    } catch (e) {
      alert('新建配置失败: ' + e.message);
    }
  }

  async deleteCurrent() {
    const c = this.getSelected();
    if (!c) return;
    if (this.configs.length <= 1) {
      alert('至少要保留一个配置, 不能删光。');
      return;
    }
    if (!confirm(`确认删除配置「${c.name}」?\n(规则会一起删除, 无法恢复)`)) return;
    try {
      const r = await api.configsDelete(c.id);
      this.configs = this.configs.filter((x) => x.id !== c.id);
      if (this.selectedId === c.id) {
        this.selectedId = r.selected_id || (this.configs[0] && this.configs[0].id);
      }
      this.dirty = false;
      this._suspendDirty = true;
      this.renderAll();
      this._suspendDirty = false;
      this.onRulesChange(this.getSelectedRules());
    } catch (e) {
      alert('删除失败: ' + e.message);
    }
  }

  async save() {
    const c = this.getSelected();
    if (!c) return;
    try {
      const updated = await api.configsPatch(c.id, { rules: c.rules });
      Object.assign(c, updated);
      this.dirty = false;
      this.renderTabs();
      this.renderActions();
    } catch (e) {
      alert('保存失败: ' + e.message);
    }
  }

  // ===== 渲染 =====
  renderAll() {
    this.renderTabs();
    this.renderRules();
    this.renderActions();
  }

  renderTabs() {
    this.tabsEl.innerHTML = '';
    for (const c of this.configs) {
      const btn = document.createElement('button');
      btn.className = 'cfg-tab' + (c.id === this.selectedId ? ' active' : '');
      btn.dataset.cid = c.id;
      btn.innerHTML = `<span class="cfg-name">${escapeHtml(c.name)}</span>${c.id === this.selectedId && this.dirty ? '<span class="cfg-dirty">●</span>' : ''}`;
      btn.title = c.id === this.selectedId && this.dirty
        ? `${c.name} (有未保存修改) · 点名字改名, 点外面切换`
        : `${c.name} · 点名字改名, 点外面切换`;
      btn.addEventListener('click', (e) => {
        if (e.target.classList.contains('cfg-name')) return;
        this.selectConfig(c.id);
      });
      const nameEl = btn.querySelector('.cfg-name');
      nameEl.addEventListener('click', (e) => {
        e.stopPropagation();
        if (c.id !== this.selectedId) {
          this.selectConfig(c.id).then(() => this.startRename(c.id));
        } else {
          this.startRename(c.id);
        }
      });
      this.tabsEl.appendChild(btn);
    }
    const add = document.createElement('button');
    add.className = 'cfg-tab-add';
    add.title = '新建配置 (自动命名, 可点页签改名)';
    add.textContent = '+';
    add.addEventListener('click', () => this.createConfig());
    this.tabsEl.appendChild(add);
  }

  startRename(cid) {
    const tabBtn = this.tabsEl.querySelector(`.cfg-tab[data-cid="${cid}"]`);
    if (!tabBtn) return;
    const nameEl = tabBtn.querySelector('.cfg-name');
    if (!nameEl) return;
    const c = this.configs.find((x) => x.id === cid);
    if (!c) return;

    const oldName = c.name;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'cfg-rename-input';
    input.value = oldName;
    input.maxLength = 50;
    nameEl.replaceWith(input);
    input.focus();
    input.select();

    let committed = false;
    const commit = async (saveIt) => {
      if (committed) return;
      committed = true;
      const newName = (input.value || '').trim().slice(0, 50);
      input.replaceWith(nameEl);
      if (!saveIt || !newName || newName === oldName) {
        nameEl.textContent = oldName;
        return;
      }
      c.name = newName;
      nameEl.textContent = newName;
      this.renderTabs();
      try {
        const updated = await api.configsPatch(cid, { name: newName });
        Object.assign(c, updated);
        this.renderTabs();
      } catch (e) {
        alert('改名失败: ' + e.message);
        c.name = oldName;
        this.renderTabs();
      }
    };

    input.addEventListener('blur', () => commit(true));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      else if (e.key === 'Escape') { e.preventDefault(); committed = true; input.replaceWith(nameEl); nameEl.textContent = oldName; }
    });
  }

  renderActions() {
    const c = this.getSelected();
    if (!c) { this.actionsEl.innerHTML = ''; return; }
    const showSave = this.dirty;
    this.actionsEl.innerHTML = `
      <button class="btn-config btn-cfg-save ${showSave ? '' : 'hidden'}" id="cfg-save-btn" title="保存当前配置的修改">💾 保存</button>
      <button class="btn-config btn-cfg-del" id="cfg-del-btn" title="删除当前配置" ${this.configs.length <= 1 ? 'disabled' : ''}>🗑️ 删除</button>
    `;
    const save = $('#cfg-save-btn', this.actionsEl);
    const del = $('#cfg-del-btn', this.actionsEl);
    if (save) save.addEventListener('click', () => this.save());
    if (del)  del.addEventListener('click', () => this.deleteCurrent());
  }

  renderRules() {
    const c = this.getSelected();
    if (!c) {
      this.listEl.innerHTML = `<div class="rule-empty">加载中…</div>`;
      return;
    }
    if (!c.rules.length) {
      this.listEl.innerHTML = `<div class="rule-empty">点下方"添加规则"开始配置</div>`;
      return;
    }

    this.listEl.innerHTML = '';

    // 表头
    const head = document.createElement('div');
    head.className = 'rule rule-head';
    head.innerHTML = `
      <span class="ix">#</span>
      <span class="rh">周期</span>
      <span class="rh">指标</span>
      <span class="rh">形态</span>
      <span class="rh">查询k线数</span>
      <span class="rh">符合k线数</span>
      <span class="rh rh-value hidden">阈值</span>
      <span class="rh rh-act"></span>
    `;
    this.listEl.appendChild(head);

    c.rules.forEach((r, i) => {
      const div = document.createElement('div');
      div.className = 'rule';
      const indDef = this.indicators.find((x) => x.key === r.indicator);
      const patDef = indDef ? indDef.patterns.find((p) => p.key === r.pattern) : null;
      const valueRequired = !!(patDef && patDef.value_required);
      const valueLabel = (patDef && patDef.value_label) || '数值';
      const lookbackMin = (patDef && patDef.lookback_min) || 30;
      const matchDefault = (patDef && patDef.match_count_default) || 1;
      const valueMin = (patDef && patDef.value_min != null) ? patDef.value_min : null;
      // 兜底: 渲染时如果当前 lookback 小于该 pattern 的 min, 抬高
      if (r.lookback < lookbackMin) r.lookback = lookbackMin;
      if (!r.match_count || r.match_count < 1) r.match_count = matchDefault;

      div.innerHTML = `
        <span class="ix">#${i + 1}</span>
        <select class="iv-interval" aria-label="周期">
          ${this._intervalOpts(r.interval)}
        </select>
        <select class="iv-indicator" aria-label="指标">
          ${this.indicators.map((x) => `<option value="${x.key}" ${x.key === r.indicator ? 'selected' : ''}>${escapeHtml(x.label)}</option>`).join('')}
        </select>
        <select class="iv-pattern" aria-label="形态">
          ${this._patternOpts(indDef, r.pattern)}
        </select>
        <input class="iv-lookback" type="number" min="${lookbackMin}" max="2000" step="1" value="${r.lookback}" title="至少 ${lookbackMin} 根 (该形态的指标最低需要)" aria-label="查询K线数" />
        <input class="iv-match" type="number" min="1" max="500" step="1" value="${r.match_count}" title="在最近 lookback 根里有 ${r.match_count} 根满足" aria-label="符合K线数" />
        <input class="iv-value ${valueRequired ? '' : 'hidden'}" type="number" step="any" ${valueMin != null ? `min="${valueMin}"` : ''} placeholder="${escapeHtml(valueLabel)}" value="${r.value ?? ''}" title="${escapeHtml(valueLabel)}${valueMin != null ? ' (最小 ' + valueMin + ')' : ''}" aria-label="${escapeHtml(valueLabel)}" />
        <button class="remove" aria-label="删除" title="删除这条规则">✕</button>
      `;
      div.querySelector('.iv-interval').addEventListener('change', (e) => this.setRule(i, { interval: e.target.value }));
      div.querySelector('.iv-indicator').addEventListener('change', (e) => this.setRule(i, { indicator: e.target.value }));
      div.querySelector('.iv-pattern').addEventListener('change', (e) => this.setRule(i, { pattern: e.target.value }));
      div.querySelector('.iv-lookback').addEventListener('input', (e) => {
        const v = parseInt(e.target.value, 10);
        this.setRule(i, { lookback: Number.isFinite(v) && v >= lookbackMin ? v : lookbackMin });
      });
      div.querySelector('.iv-lookback').addEventListener('blur', (e) => {
        let v = parseInt(e.target.value, 10);
        if (!Number.isFinite(v) || v < lookbackMin) {
          v = lookbackMin;
          e.target.value = v;
          this.setRule(i, { lookback: v });
        }
      });
      div.querySelector('.iv-match').addEventListener('input', (e) => {
        const v = parseInt(e.target.value, 10);
        this.setRule(i, { match_count: Number.isFinite(v) && v >= 1 ? v : 1 });
      });
      div.querySelector('.iv-match').addEventListener('blur', (e) => {
        let v = parseInt(e.target.value, 10);
        if (!Number.isFinite(v) || v < 1) { v = 1; e.target.value = v; this.setRule(i, { match_count: v }); }
      });
      const valEl = div.querySelector('.iv-value');
      valEl.addEventListener('input', (e) => {
        const raw = e.target.value;
        if (raw === '' || raw === '-') {
          this.setRule(i, { value: null });
          return;
        }
        const v = parseFloat(raw);
        if (Number.isFinite(v)) {
          // 低于 value_min 时仍然接受, 让用户继续输入 (例如先打 0. 想打 0.5)
          // 真正的 clamp 在 blur 时做
          this.setRule(i, { value: v });
        }
      });
      valEl.addEventListener('blur', (e) => {
        let v = parseFloat(e.target.value);
        if (!Number.isFinite(v)) return;
        if (valueMin != null && v < valueMin) {
          v = valueMin;
          e.target.value = String(v);
          this.setRule(i, { value: v });
        }
      });
      div.querySelector('.remove').addEventListener('click', () => this.removeRule(i));
      this.listEl.appendChild(div);
    });
  }

  _intervalOpts(current) {
    const opts = ['<option value="">— 周期 —</option>'];
    for (const iv of this.intervals) {
      opts.push(`<option value="${iv}" ${iv === current ? 'selected' : ''}>${iv}</option>`);
    }
    return opts.join('');
  }

  _patternOpts(indDef, current) {
    if (!indDef || !indDef.patterns || !indDef.patterns.length) {
      return `<option value="">— 选指标后填 —</option>`;
    }
    const opts = [`<option value="">— 形态 —</option>`];
    for (const p of indDef.patterns) {
      opts.push(`<option value="${p.key}" ${p.key === current ? 'selected' : ''}>${escapeHtml(p.label)}</option>`);
    }
    return opts.join('');
  }

  _markDirty() {
    if (this._suspendDirty) return;
    this.dirty = true;
    this.renderTabs();
    this.renderActions();
    this.onRulesChange(this.getSelectedRules());
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}