# BOLL 筛选器

A 股 / Binance 永续合约的 BOLL 形态筛选应用。  
前端: Vanilla JS PWA · 后端: Python FastAPI + AKShare

> 运行环境: 浏览器 (推荐 Chrome / Safari) · iOS Safari 可"添加到主屏幕"当 PWA 用。

---

## 功能

- **多周期 K 线筛选**: 3m / 5m / 15m / 30m / 1h / 2h / 4h / 1d / 3d / 1w
- **BOLL 形态检测**:
  - 布林带开口 (带宽扩张)
  - 向上穿越中轨 / 向下穿越中轨
  - 中轨上行 / 中轨下行
  - 突破上轨 / 跌破下轨
- **多周期联合判断**: 例如 15m + 30m + 1h + 4h 全部满足"向上穿越中轨"
- **两市场**:
  - A 股 (沪深京, 数据源: AKShare)
  - Binance USDT 永续合约 (公开 API, 无需鉴权)
- **PWA**: 可安装到 iOS 主屏幕, 离线访问壳页面
- **K 线预览**: 命中标的可在弹窗内查看 K 线 + BOLL 三轨 (基于 lightweight-charts)

---

## 项目结构

```
CryptoTrade/
├── backend/                    Python 后端
│   ├── main.py                 FastAPI 入口
│   ├── indicators.py           BOLL 指标计算
│   ├── data_sources/
│   │   ├── a_share.py          AKShare 集成
│   │   └── crypto.py           Binance 公开 API
│   ├── scanner/
│   │   ├── matcher.py          形态匹配器
│   │   └── service.py          扫描服务
│   └── requirements.txt
├── frontend/                   PWA 前端 (Vanilla JS)
│   ├── index.html
│   ├── manifest.webmanifest
│   ├── sw.js                   Service Worker
│   ├── css/style.css
│   ├── js/
│   │   ├── app.js              应用入口
│   │   ├── api.js              API 客户端
│   │   ├── filters.js          筛选器 UI
│   │   └── chart.js            K 线图组件
│   └── icons/
├── scripts/
│   ├── start-backend.sh        macOS / Linux 启动
│   └── start-backend.bat       Windows 启动
└── README.md
```

---

## 启动

### 1. 后端 (Python 3.10+)

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python main.py
```

服务监听 `http://localhost:8000`。  
也可以直接用 `scripts/start-backend.bat` (Windows) 或 `scripts/start-backend.sh` (macOS/Linux)。

### 2. 前端

前端就是静态文件, 由 FastAPI 在 `/` 直接提供。  
浏览器打开 `http://localhost:8000` 即可。

> iOS 使用: 用 Safari 打开上面的地址 → 分享 → "添加到主屏幕"。  
> 之后可像原生 App 一样启动, 并具备离线壳能力 (Service Worker)。

---

## API 速查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | `/api/health`              | 健康检查 |
| GET  | `/api/patterns`            | 列出所有支持的形态 |
| GET  | `/api/a-stocks/list`       | A 股列表 |
| GET  | `/api/a-stocks/kline?symbol=000001&interval=1d` | 单只 A 股 K 线 (含 BOLL) |
| GET  | `/api/crypto/list`         | Binance 永续合约列表 |
| GET  | `/api/crypto/kline?symbol=BTCUSDT&interval=1h` | 单只币 K 线 (含 BOLL) |
| POST | `/api/scan`                | 按规则扫描, 见下 |

### POST /api/scan 请求体

```json
{
  "market": "crypto",
  "rules": [
    { "interval": "15m", "pattern": "cross_mid_up" },
    { "interval": "30m", "pattern": "cross_mid_up" },
    { "interval": "1h",  "pattern": "cross_mid_up" },
    { "interval": "4h",  "pattern": "cross_mid_up" }
  ],
  "combine": "all",
  "limit": 100,
  "concurrency": 8,
  "symbols": null
}
```

- `market`: `"a_share"` | `"crypto"`
- `combine`: `"all"` (AND, 全部满足) | `"any"` (OR, 任意一条)
- `symbols`: 不传 = 全市场扫描; 传 = 只扫列表内的标的
- `limit`: 每只 K 线拉取根数, 默认 100 (BOLL 20 周期 + 余量)
- `concurrency`: 并发数, 默认 8

### 形态 key

| key | 含义 |
| --- | --- |
| `boll_open`        | 布林带开口 (带宽扩张) |
| `cross_mid_up`     | 收盘价由下方穿越中轨向上 |
| `cross_mid_down`   | 收盘价由上方穿越中轨向下 |
| `mid_trend_up`     | 中轨本身趋势向上 |
| `mid_trend_down`   | 中轨本身趋势向下 |
| `upper_breakout`   | 突破上轨 |
| `lower_breakout`   | 跌破下轨 |

---

## 注意事项

- **A 股** 走 AKShare, 第一次扫描会较慢 (5000+ 只股票 × 多个周期), 建议先用"仅扫描前 50 个"按钮试效果。
- **Binance** 数据通过 `https://fapi.binance.com` 拉取, 公开 API 免鉴权; 部分地区可能需要代理, 部署到服务器时注意。
- **前端 K 线预览** 使用 CDN 加载 `lightweight-charts`; 离线安装后第一次需联网拉取。
- **形态判定** 都是基于最新一根已收盘 K 线 + 前一根的关系; 当前未实现"盘中实时 tick 触发"。

---

## 二次开发提示

- 想加新形态?  在 `backend/scanner/matcher.py` 里加 handler + 在 `PATTERN_LABELS` 加中文名, 前端自动同步。
- 想加新市场?  在 `backend/data_sources/` 加一个数据源文件, 在 `scanner/service.py` 里分发即可。
- 想要更强的并发?  调整 `concurrency` 字段 (1-32), 或在 `service.py` 里改 `BATCH` 大小。
