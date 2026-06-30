// 筛选器 UI 管理

// 可选时间周期 (Binance 全 + A 股常用)
export const INTERVALS = [
  { v: '3m',  label: '3 分钟' },
  { v: '5m',  label: '5 分钟' },
  { v: '15m', label: '15 分钟' },
  { v: '30m', label: '30 分钟' },
  { v: '1h',  label: '1 小时' },
  { v: '2h',  label: '2 小时' },
  { v: '4h',  label: '4 小时' },
  { v: '1d',  label: '日线' },
  { v: '3d',  label: '3 日' },
  { v: '1w',  label: '周线' },
];

// 默认形态 - 启动时硬编码兜底, 真实列表从 /api/patterns 拉取
export const DEFAULT_PATTERNS = [
  { v: 'cross_mid_up',    label: '向上穿越中轨' },
  { v: 'cross_mid_down',  label: '向下穿越中轨' },
  { v: 'boll_open',       label: 'BOLL 开口' },
  { v: 'mid_trend_up',    label: '中轨上行' },
  { v: 'mid_trend_down',  label: '中轨下行' },
  { v: 'upper_breakout',  label: '突破上轨' },
  { v: 'lower_breakout',  label: '跌破下轨' },
];

export class FilterManager {
  constructor(rootEl) {
    this.root = rootEl;
    this.rules = [];
    this.patterns = DEFAULT_PATTERNS;
    this.onChange = () => {};
    this.render();
  }

  setPatterns(list) {
    if (Array.isArray(list) && list.length) {
      this.patterns = list.map((p) => ({ v: p.key, label: p.label }));
      this.render();
    }
  }

  add(rule = {}) {
    this.rules.push({
      interval: rule.interval || '15m',
      pattern: rule.pattern || 'cross_mid_up',
    });
    this.render();
    this.onChange();
  }

  remove(i) {
    this.rules.splice(i, 1);
    this.render();
    this.onChange();
  }

  set(i, patch) {
    this.rules[i] = { ...this.rules[i], ...patch };
    this.onChange();
  }

  clear() {
    this.rules = [];
    this.render();
    this.onChange();
  }

  getRules() {
    return this.rules.map((r) => ({ ...r }));
  }

  setRules(rules) {
    this.rules = (rules || []).map((r) => ({ ...r }));
    this.render();
    this.onChange();
  }

  render() {
    if (!this.rules.length) {
      this.root.innerHTML = `<div class="rule-empty">点击右上角 "+ 添加规则" 开始配置</div>`;
      return;
    }
    this.root.innerHTML = '';
    this.rules.forEach((r, i) => {
      const div = document.createElement('div');
      div.className = 'rule';
      div.innerHTML = `
        <span class="ix">#${i + 1}</span>
        <select class="interval" aria-label="时间周期">
          ${INTERVALS.map((iv) => `<option value="${iv.v}" ${iv.v === r.interval ? 'selected' : ''}>${iv.label}</option>`).join('')}
        </select>
        <span class="muted small">→</span>
        <select class="pattern" aria-label="形态">
          ${this.patterns.map((p) => `<option value="${p.v}" ${p.v === r.pattern ? 'selected' : ''}>${p.label}</option>`).join('')}
        </select>
        <button class="remove" aria-label="删除">✕</button>
      `;
      div.querySelector('.interval').addEventListener('change', (e) => this.set(i, { interval: e.target.value }));
      div.querySelector('.pattern').addEventListener('change', (e) => this.set(i, { pattern: e.target.value }));
      div.querySelector('.remove').addEventListener('click', () => this.remove(i));
      this.root.appendChild(div);
    });
  }
}
