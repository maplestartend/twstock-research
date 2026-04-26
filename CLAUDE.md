# Claude 工作守則（本專案專用）

> 給未來進來的 Claude session 看的。讀完先動工。

## 文件入口
- 使用方式 / 頁面用法：[USAGE.md](USAGE.md)
- 架構與限制：[docs/architecture.md](docs/architecture.md)
- API 規格：[docs/api-spec.md](docs/api-spec.md)
- 前端規格：[docs/frontend-spec.md](docs/frontend-spec.md)
- 維運手冊（含 dev/build 地雷）：[docs/operations.md](docs/operations.md)

## 必踩才會記得的地雷

### 1. 不要在 `next dev` 跑著的時候跑 `npm run build`
兩者共用 `web/.next/`，並行寫會互相覆蓋 → dev server 直接 500、`Cannot find module './XXX.js'`。
要量 bundle size 必須**先收掉 dev**，build 完再起回去。
細節與修法見 [docs/operations.md 「前端 dev / build 工序」一節](docs/operations.md)。

### 2. Treemap 用 d3-hierarchy，不要用 Recharts
Recharts 的 squarify 在 Taiwan 產業熱力圖那種「最大磚 vs 最小磚比 1000:1」的資料上會 layout 崩掉。其他線圖 / 面積圖 / scatter 仍用 Recharts。

### 3. UI 改動必須用 Playwright 拍圖驗收
type-check 過 + build 過不代表畫面對。三支常駐 regression 腳本：
- [web/scripts/screenshot.mjs](web/scripts/screenshot.mjs) — sectors heatmap layout 驗收（含 hover popup + d3 tile aspect-ratio assertion）
- [web/scripts/screenshot-all.mjs](web/scripts/screenshot-all.mjs) — 全頁 light / dark 覆蓋
- [web/scripts/screenshot-mobile.mjs](web/scripts/screenshot-mobile.mjs) — 手機 drawer / responsive padding

新功能驗收要不就接到上面三支裡，要不就臨時寫一支拍完刪掉——不要再留 `screenshot-s2-*` / `screenshot-validation` 這種 Sprint-tag 一次性腳本累積成垃圾。

### 4. 改功能要同步更新 [USAGE.md](USAGE.md) / requirements / .bat
別只動程式碼忘了改文件。新增 endpoint 也要更新 [docs/api-spec.md](docs/api-spec.md)。

### 5. 改 `app/scoring/*` 後跑 `restart.bat`
`signal_history` 表是「上次 market_update 跑 score_all 寫進去的快照」。`ensure_fresh()` 只在 `as_of < daily_price.MAX(date)` 時才重跑——engine 程式改了但日期沒變的話**不會自動重算**。雷達/自選讀的還是舊邏輯算的快照、個股詳情即時呼叫新 engine，於是兩邊分數對不上。`restart.bat` 會 stop → 強制 `snapshot_today()` → relaunch。

注意：`score_stock`（個股詳情）跟 `score_all`（雷達/自選快照）必須吃同一個 `fund_snap`。`score_all` 在 [radar.py](app/scoring/radar.py) 注入 `dividend_yield_z`（同產業殖利率 z-score），`score_stock` 在 [engine.py](app/scoring/engine.py) 也呼叫 `industry_yield_z_for_stock` 注入。改 rubric 時若新增「批次預載」的欄位，兩邊都要補。

### 6. .bat 工具
公開（雙擊）：`launch` / `stop` / `restart` / `status` / `daily-update` / `install-schedule` / `uninstall-schedule`。私有（被 call）：`_launch-servers` / `_kill-servers`。改 stop / restart 共用的殺 process 邏輯時改 `_kill-servers.bat`，兩邊都會吃到。

## 跑測試

```bash
python -m pytest tests/ -q     # 265 passed (含 ATR、漲跌停連板 pending exit、daily MTM MDD、ETF 稅率、月營收 publish-date migration、score_all as_of、event-backtest split/dividend、foreign_mid % of ADV、stock_info.is_tradable migration + warrant 兜底)
cd web && npx tsc --noEmit     # frontend type check
```

修 `app/scoring/`、`app/backtest/`、`app/risk.py`、`api/routers/`、`app/data/adjuster.py` 之前先跑過一次。

## 台股慣例（會出現在 UI 與資料）

- 紅漲綠跌（與美股顏色相反）
- 漲跌停 ±10%（以前一日收盤計算），開盤即漲跌停 = 流動性蒸發
- 0050 是 ETF 代號 4 碼，大多數股票代號 4 碼（上市/上櫃）
- 月營收**最遲次月 10 號公告**（系統 publish_date 也以 10 號 stamp，避免 look-ahead bias），季財報 5/14、8/14、11/14、3/31 公告
- 證交稅賣方依代號分流：**一般股 0.3% / 股票型 ETF 0.1% / 債券 ETF (00xxxB) 0%** — 統一走 `tax_rate_for(stock_id)` in [app/portfolio.py](app/portfolio.py)，回測引擎、持股估值、新增交易自動扣稅都吃這個 helper。手續費雙向 0.1425%（券商通常會折扣）。

## 資料源限制

- TWSE / TPEX OpenAPI：免費但只給「日 final」資料，盤後 16:30 才完整
- FinMind 免費版：`TaiwanStockFinancialStatements`、`TaiwanStockDividendResult`、`TaiwanStockSplitPrice` 三個免費 endpoint，其他付費別碰
- MOPS：POST 需帶 Referer header，IP 被 rate-limit 等幾分鐘再跑
