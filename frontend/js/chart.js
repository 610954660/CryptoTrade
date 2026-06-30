// K 线图组件 - 使用 lightweight-charts
// 负责: 拉取 K 线 + BOLL 数据 -> 渲染主图(蜡烛) + 叠加(中/上/下轨)

const INTERVAL_OPTIONS = [
  { v: '3m',  label: '3m' },
  { v: '5m',  label: '5m' },
  { v: '15m', label: '15m' },
  { v: '30m', label: '30m' },
  { v: '1h',  label: '1h' },
  { v: '2h',  label: '2h' },
  { v: '4h',  label: '4h' },
  { v: '1d',  label: '1d' },
  { v: '3d',  label: '3d' },
  { v: '1w',  label: '1w' },
];

export class KLineChart {
  constructor({ modal, container, titleEl, subEl, intervalsEl, infoEl, closeBtn, api }) {
    this.modal = modal;
    this.container = container;
    this.titleEl = titleEl;
    this.subEl = subEl;
    this.intervalsEl = intervalsEl;
    this.infoEl = infoEl;
    this.closeBtn = closeBtn;
    this.api = api;
    this.chart = null;
    this.series = {};
    this.current = null;

    this.closeBtn.addEventListener('click', () => this.hide());
    this.modal.addEventListener('click', (e) => {
      if (e.target === this.modal) this.hide();
    });

    this._buildIntervalBar();
  }

  _buildIntervalBar() {
    this.intervalsEl.innerHTML = '';
    INTERVAL_OPTIONS.forEach((iv) => {
      const b = document.createElement('button');
      b.className = 'interval-btn';
      b.textContent = iv.label;
      b.dataset.value = iv.v;
      b.addEventListener('click', () => this._loadInterval(iv.v));
      this.intervalsEl.appendChild(b);
    });
  }

  show(market, symbol, displayName, defaultInterval, provider = 'binance') {
    this.current = { market, symbol, displayName, provider };
    this.titleEl.textContent = displayName || symbol;
    const marketLabel = market === 'a_share' ? 'A 股' :
                        provider === 'okx' ? 'OKX' : 'Binance';
    this.subEl.textContent = `市场: ${marketLabel} · 代码: ${symbol}`;
    this.modal.classList.remove('hidden');
    this._highlightInterval(defaultInterval || '1d');
    this._loadInterval(defaultInterval || '1d');
  }

  hide() {
    this.modal.classList.add('hidden');
    if (this.chart) {
      this.chart.remove();
      this.chart = null;
      this.series = {};
    }
  }

  _highlightInterval(v) {
    this.intervalsEl.querySelectorAll('.interval-btn').forEach((b) => {
      b.classList.toggle('active', b.dataset.value === v);
    });
  }

  async _loadInterval(interval) {
    if (!this.current) return;
    this._highlightInterval(interval);
    const { market, symbol, provider } = this.current;
    this.infoEl.textContent = '加载中…';
    try {
      let data;
      if (market === 'a_share') {
        data = await this.api.aShareKline(symbol, interval, 200);
      } else {
        data = await this.api.cryptoKline(symbol, interval, 200, provider);
      }
      this._render(data, interval);
    } catch (e) {
      this.infoEl.textContent = `加载失败: ${e.message}`;
    }
  }

  _render(data, interval) {
    // 清理旧 chart
    if (this.chart) {
      this.chart.remove();
      this.chart = null;
      this.series = {};
    }
    if (!data || !data.klines || !data.klines.length) {
      this.infoEl.textContent = '无 K 线数据';
      return;
    }
    // 时间转秒 (Binance 是毫秒, A 股是秒; 通过数量级判断)
    const candles = data.klines.map((k) => {
      const t = k[0] > 1e12 ? Math.floor(k[0] / 1000) : k[0];
      return { time: t, open: k[1], high: k[2], low: k[3], close: k[4] };
    });
    const boll = (data.boll || []).map((p) => ({
      time: p.time > 1e12 ? Math.floor(p.time / 1000) : p.time,
      value: p.mid,
    }));
    const upper = (data.boll || []).map((p) => ({
      time: p.time > 1e12 ? Math.floor(p.time / 1000) : p.time,
      value: p.upper,
    }));
    const lower = (data.boll || []).map((p) => ({
      time: p.time > 1e12 ? Math.floor(p.time / 1000) : p.time,
      value: p.lower,
    }));

    this.chart = LightweightCharts.createChart(this.container, {
      width: this.container.clientWidth,
      height: this.container.clientHeight,
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#8a96ad',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.04)' },
      },
      rightPriceScale: { borderColor: '#23304a' },
      timeScale: { borderColor: '#23304a', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
    });

    const candleSeries = this.chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    });
    candleSeries.setData(candles);

    const midSeries = this.chart.addLineSeries({
      color: '#f59e0b', lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
    });
    midSeries.setData(boll);

    const upperSeries = this.chart.addLineSeries({
      color: 'rgba(239, 68, 68, 0.6)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    upperSeries.setData(upper);

    const lowerSeries = this.chart.addLineSeries({
      color: 'rgba(34, 197, 94, 0.6)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    lowerSeries.setData(lower);

    this.chart.timeScale().fitContent();
    this.series = { candleSeries, midSeries, upperSeries, lowerSeries };

    // 信息条
    if (data.boll && data.boll.length) {
      const last = data.boll[data.boll.length - 1];
      const c = candles[candles.length - 1];
      this.infoEl.innerHTML = `
        <div>周期: <b>${interval}</b> · 共 ${data.klines.length} 根 K 线</div>
        <div>收盘: <b>${c.close.toFixed(4)}</b> · 中轨: <b>${last.mid.toFixed(4)}</b> · 带宽: <b>${(last.width * 100).toFixed(2)}%</b></div>
        <div>上轨: <b>${last.upper.toFixed(4)}</b> · 下轨: <b>${last.lower.toFixed(4)}</b></div>
      `;
    } else {
      this.infoEl.textContent = `周期: ${interval} · ${data.klines.length} 根 K 线`;
    }
  }
}
