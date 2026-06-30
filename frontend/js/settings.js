// 设置弹窗: 代理 / 开关 两个页签
// 用法: import { openSettings } from './settings.js'; openSettings();
import { api } from './api.js';

const HTML = `
<div id="settings-modal" class="modal hidden" role="dialog" aria-modal="true">
  <div class="modal-card settings-card">
    <div class="modal-head">
      <div>
        <h3>⚙️ 设置</h3>
        <div class="muted small">配置代理、缓存策略等运行时参数</div>
      </div>
      <button id="settings-close" class="icon-btn" aria-label="关闭">✕</button>
    </div>
    <div class="settings-body">
      <!-- 页签栏 -->
      <div class="settings-tabs">
        <button class="stab active" data-tab="proxy">🌐 代理</button>
        <button class="stab" data-tab="switches">🔘 开关</button>
      </div>

      <!-- ===== 代理页 ===== -->
      <div class="stab-panel" data-panel="proxy">
        <p class="muted small" style="margin-top:0">
          Crypto 路径默认走代理 (Binance / OKX 在大陆地区有 451 风险);
          A 股数据始终直连国内站点, 不受代理影响。
        </p>

        <div class="form-row">
          <label class="switch">
            <input type="checkbox" id="opt-proxy-enabled" />
            <span class="track"></span>
            <span class="lbl">启用代理</span>
          </label>
        </div>

        <div class="form-row">
          <label for="opt-proxy-url">代理 URL</label>
          <input type="text" id="opt-proxy-url" placeholder="http://127.0.0.1:7897  或  socks5://127.0.0.1:1080" />
        </div>

        <div class="form-row">
          <label for="opt-proxy-scope">作用范围</label>
          <select id="opt-proxy-scope">
            <option value="crypto_only">仅 Crypto (推荐)</option>
            <option value="all">所有出站请求</option>
          </select>
        </div>

        <div class="form-actions">
          <button id="btn-proxy-test" class="btn ghost">🔌 测试代理</button>
          <span id="proxy-test-result" class="muted small"></span>
        </div>

        <div class="form-actions">
          <button id="btn-proxy-save" class="btn primary">💾 保存</button>
          <span id="proxy-save-result" class="muted small"></span>
        </div>

        <details class="settings-help">
          <summary>支持的代理协议</summary>
          <ul>
            <li><code>http://host:port</code> — 普通 HTTP 代理 (如 Clash、v2rayN HTTP 端口)</li>
            <li><code>socks5://host:port</code> — SOCKS5 代理 (需先 <code>pip install httpx[socks]</code>)</li>
            <li>留空 = 走直连</li>
          </ul>
        </details>
      </div>

      <!-- ===== 开关页 ===== -->
      <div class="stab-panel hidden" data-panel="switches">
        <p class="muted small" style="margin-top:0">
          缓存和预热相关控制。变更后立即生效。
        </p>

        <div class="switch-row">
          <div>
            <div class="row-title">🚀 启动自预热</div>
            <div class="row-sub muted small">
              后台拉取 a_share / crypto / crypto_okx 三个市场的前 200 个标的的 1d K 线入缓存。
              跑完后用户扫描会直接命中缓存, 速度快。 跑中可以随时停。
            </div>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <button id="btn-prewarm-start" class="btn primary">启动</button>
            <button id="btn-prewarm-stop" class="btn ghost" disabled>停止</button>
          </div>
        </div>
        <div id="prewarm-progress" class="hidden" style="margin-bottom:12px">
          <div class="progress-bar"><div id="prewarm-fill" class="progress-fill"></div></div>
          <div id="prewarm-text" class="muted small" style="margin-top:6px">—</div>
        </div>

        <div class="switch-row">
          <div>
            <div class="row-title">📡 数据直连 (不走缓存)</div>
            <div class="row-sub muted small">
              打开后: 标的列表 / K 线都直接走数据源, 不读不写缓存。<br>
              调试 / 实时性优先时使用。关闭后恢复缓存策略。
            </div>
          </div>
          <label class="switch">
            <input type="checkbox" id="opt-no-cache" />
            <span class="track"></span>
          </label>
        </div>

        <div id="switch-status" class="muted small" style="margin-top:12px"></div>
      </div>
    </div>
  </div>
</div>
`;

let mounted = false;
let onChangeCb = null;

function $(sel, root = document) { return root.querySelector(sel); }

async function loadIntoUI() {
  try {
    const s = await api.settingsGet();
    const p = s.proxy || {};
    $('#opt-proxy-enabled', root()).checked = !!p.enabled;
    $('#opt-proxy-url', root()).value = p.url || '';
    $('#opt-proxy-scope', root()).value = p.scope || 'crypto_only';
    $('#opt-no-cache', root()).checked = !!((s.runtime || {}).no_cache);
  } catch (e) {
    showMsg('#proxy-save-result', '加载失败: ' + e.message, true);
  }
}

function root() { return document.getElementById('settings-modal'); }

function showMsg(sel, text, isErr = false) {
  const el = $(sel, root());
  if (!el) return;
  el.textContent = text;
  el.style.color = isErr ? 'var(--red)' : 'var(--green)';
  if (text) setTimeout(() => { if (el.textContent === text) el.textContent = ''; }, 5000);
}

async function saveProxy() {
  const enabled = $('#opt-proxy-enabled', root()).checked;
  const url = $('#opt-proxy-url', root()).value.trim();
  const scope = $('#opt-proxy-scope', root()).value;
  try {
    await api.settingsPut({ proxy: { enabled, url, scope } });
    showMsg('#proxy-save-result', '已保存 · 下次 crypto 请求生效');
    if (onChangeCb) onChangeCb();
  } catch (e) {
    showMsg('#proxy-save-result', '保存失败: ' + e.message, true);
  }
}

async function testProxy() {
  const btn = $('#btn-proxy-test', root());
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '测试中…';
  showMsg('#proxy-test-result', '');
  try {
    const r = await api.settingsTestProxy();
    showMsg('#proxy-test-result', r.ok ? '✅ ' + r.message : '❌ ' + r.message, !r.ok);
  } catch (e) {
    showMsg('#proxy-test-result', '❌ ' + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function saveNoCache() {
  const v = $('#opt-no-cache', root()).checked;
  try {
    await api.settingsPut({ runtime: { no_cache: v } });
    const box = $('#switch-status', root());
    box.textContent = v ? '⚡ 已开启直连模式' : '已恢复缓存模式';
    box.style.color = v ? 'var(--amber)' : 'var(--green)';
    if (onChangeCb) onChangeCb();
  } catch (e) {
    alert('失败: ' + e.message);
  }
}

let prewarmPoller = null;

function setPrewarmUI(state) {
  const startBtn = $('#btn-prewarm-start', root());
  const stopBtn = $('#btn-prewarm-stop', root());
  const box = $('#prewarm-progress', root());
  const fill = $('#prewarm-fill', root());
  const text = $('#prewarm-text', root());

  if (!state || !state.running) {
    startBtn.disabled = false;
    startBtn.textContent = '🚀 启动预热';
    stopBtn.disabled = true;
    box.classList.add('hidden');
    if (prewarmPoller) { clearInterval(prewarmPoller); prewarmPoller = null; }
    return;
  }

  // running = true
  startBtn.disabled = true;
  startBtn.textContent = '预热中…';
  stopBtn.disabled = false;
  box.classList.remove('hidden');

  const pm = state.per_market || {};
  let done = 0, total = 0, err = 0;
  for (const m of Object.keys(pm)) {
    done += pm[m].done || 0;
    total += pm[m].total || 0;
    err += pm[m].errored || 0;
  }
  const pct = total ? Math.min(100, Math.round(done / total * 100)) : 0;
  fill.style.width = pct + '%';
  const startedAt = state.started_at ? new Date(state.started_at * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '';
  text.textContent = `进度: ${done} / ${total} (${pct}%) · 错误 ${err} · 启动于 ${startedAt} · ${state.cancelling ? '正在取消…' : '运行中'}`;
}

async function refreshPrewarm() {
  try {
    const s = await api.cachePrewarmStatus();
    setPrewarmUI(s);
    if (!s.running && prewarmPoller) { clearInterval(prewarmPoller); prewarmPoller = null; }
  } catch {}
}

async function startPrewarm() {
  const btn = $('#btn-prewarm-start', root());
  const stopBtn = $('#btn-prewarm-stop', root());
  const box = $('#switch-status', root());
  btn.disabled = true;
  btn.textContent = '提交中…';
  try {
    const r = await api.cachePrewarm();
    if (r.already_running) {
      box.textContent = '⚡ 预热已在运行';
    } else {
      box.textContent = '✅ 预热已启动, 后台跑…';
    }
    box.style.color = 'var(--primary)';
    setPrewarmUI(await api.cachePrewarmStatus());
    if (!prewarmPoller) prewarmPoller = setInterval(refreshPrewarm, 1500);
  } catch (e) {
    box.textContent = '❌ 启动失败: ' + e.message;
    box.style.color = 'var(--red)';
    btn.disabled = false;
    btn.textContent = '🚀 启动预热';
  }
}

async function stopPrewarm() {
  const btn = $('#btn-prewarm-stop', root());
  const box = $('#switch-status', root());
  btn.disabled = true;
  btn.textContent = '停止中…';
  try {
    await api.cachePrewarmStop();
    box.textContent = '🛑 预热已停止';
    box.style.color = 'var(--amber)';
    setPrewarmUI(await api.cachePrewarmStatus());
  } catch (e) {
    box.textContent = '❌ 停止失败: ' + e.message;
    box.style.color = 'var(--red)';
  } finally {
    btn.disabled = false;
    btn.textContent = '停止';
  }
}

function bind() {
  const r = root();
  // 关闭
  $('#settings-close', r).addEventListener('click', closeSettings);
  r.addEventListener('click', (e) => { if (e.target === r) closeSettings(); });
  // tab 切换
  r.querySelectorAll('.stab').forEach((btn) => {
    btn.addEventListener('click', () => {
      r.querySelectorAll('.stab').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const target = btn.dataset.tab;
      r.querySelectorAll('.stab-panel').forEach((p) => {
        p.classList.toggle('hidden', p.dataset.panel !== target);
      });
    });
  });
  // 代理
  $('#btn-proxy-save', r).addEventListener('click', saveProxy);
  $('#btn-proxy-test', r).addEventListener('click', testProxy);
  // 开关
  $('#opt-no-cache', r).addEventListener('change', saveNoCache);
  $('#btn-prewarm-start', r).addEventListener('click', startPrewarm);
  $('#btn-prewarm-stop', r).addEventListener('click', stopPrewarm);
}

function mount() {
  if (mounted) return;
  const div = document.createElement('div');
  div.innerHTML = HTML;
  document.body.appendChild(div.firstElementChild);
  bind();
  mounted = true;
}

export function openSettings(opts = {}) {
  mount();
  const r = root();
  r.classList.remove('hidden');
  loadIntoUI();
  onChangeCb = opts.onChange || null;
  // 拉一次预热状态, 如果有正在跑的, 接管轮询
  refreshPrewarm().then(() => {
    // 如果后端在跑, 启动前端轮询
    api.cachePrewarmStatus().then((s) => {
      if (s.running && !prewarmPoller) {
        prewarmPoller = setInterval(refreshPrewarm, 1500);
      }
    }).catch(() => {});
  });
}

export function closeSettings() {
  const r = root();
  if (r) r.classList.add('hidden');
}

// 全局 ESC 关闭
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && mounted && !root().classList.contains('hidden')) closeSettings();
});
