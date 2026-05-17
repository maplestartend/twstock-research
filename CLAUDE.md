# Claude 工作守則（本專案專用）

> 給未來進來的 Claude session 看的。讀完先動工。

## 文件入口
- 使用方式 / 頁面用法：[USAGE.md](USAGE.md)
- 架構與限制：[docs/architecture.md](docs/architecture.md)
- API 規格（手寫 narrative）：[docs/api-spec.md](docs/api-spec.md)
- API 規格（FastAPI 自動產出，single source of truth）：[docs/api-spec.json](docs/api-spec.json) — 跑 `python -m scripts.dump_openapi` 同步；CI 用 `--check` 偵測 drift
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
type-check + build 綠燈不代表畫面對。改 `web/components/`、`web/app/`、`web/lib/`、`web/styles/` 後必跑 [`web/scripts/screenshot*.mjs`](web/scripts/) 三支之一。完整 SOP（哪支對應哪種改動 + 「不要再產 `screenshot-s2-*` 一次性腳本」規則）：[`.claude/skills/ui-screenshot-verify/`](.claude/skills/ui-screenshot-verify/SKILL.md)。

### 4. 改功能要同步更新 [USAGE.md](USAGE.md) / requirements / .bat
別只動程式碼忘了改文件。新增 endpoint 也要更新 [docs/api-spec.md](docs/api-spec.md)。

### 5. 改 `app/scoring/*` / `app/backtest/*` / `app/risk.py` / `app/portfolio.py` 後跑 `restart.bat`
`signal_history` 是上次 `score_all` 寫進去的快照；engine 改了但 `as_of` 日期沒變 → 雷達/自選讀舊快照、個股詳情即時呼叫新 engine，兩邊分數會分歧。`restart.bat` 會 stop → 強制 `snapshot_today()` → relaunch。完整 SOP（含 `score_stock` 與 `score_all` 必須吃同一份 `fund_snap` 的同步規則）：[`.claude/skills/scoring-restart/`](.claude/skills/scoring-restart/SKILL.md)。

> **Snapshot 必須含基本面**：`snapshot_today` / `radar.score_all` 預設 `include_fundamentals=True`；改成 False 會讓 `signal_history.long` 整欄 NULL（雷達/自選的長期分數欄全空）。`market_update.py` 的旗標 2026-05-04 修過 — 預設改成含基本面、`--skip-snapshot-fundamentals` 才 opt-out（之前是 `--snapshot-with-fundamentals` opt-in，造成每天 daily-update 都寫壞 long）。

### 6. .bat 工具
公開（雙擊）：`launch` / `stop` / `restart` / `status` / `daily-update` / `install-schedule` / `uninstall-schedule`。私有（被 call）：`_launch-servers` / `_kill-servers`。改 stop / restart 共用的殺 process 邏輯時改 `_kill-servers.bat`，兩邊都會吃到。

### 7. ETF 還原價必須走 `daily_price_adj`、不要直接用 `daily_price.close`
`daily_price` 對 0050 / 00631L 跨年資料**還原模式不一致**（早期已還原、2025-06-18 1:4 split 之後是 raw post-split），直接拿來算 buy-and-hold 會誇大報酬 5×。FinMind 對槓桿 ETF 也沒提供 split events → `adj_event` 表對 00631L 是空的、`adjuster.py` 算不出。修補機制：[scripts/backfill_etf_adj_yfinance.py](scripts/backfill_etf_adj_yfinance.py) 用 yfinance `auto_adjust=True` 把 0050 / 00631L / 00692 / 00878 完整還原歷史灌進 `daily_price_adj`，已整合進 `daily-update.bat` 每日跑（**無條件執行**，不綁 market_update 的 RC，避免後者中途被中斷時 ETF 還原價斷層）。回測 / scoring / 過熱指標一律走 `daily_price_adj.close_adj`（或 `read_close_with_adj_coalesced` helper），不要直接讀 `daily_price.close`。

## 跑測試

```bash
python -m pytest tests/ -q     # 497 passed (2026-05-10)
cd web && npx tsc --noEmit     # frontend type check
```

修 `app/scoring/`、`app/backtest/`、`app/risk.py`、`api/routers/`、`app/data/adjuster.py` 之前先跑過一次。

## 台股慣例（會出現在 UI 與資料）

- 紅漲綠跌（與美股顏色相反）
- 漲跌停 ±10%（以前一日收盤計算），開盤即漲跌停 = 流動性蒸發
- 0050 是 ETF 代號 4 碼，大多數股票代號 4 碼（上市/上櫃）
- 休市日：週末 + 國定假日（5/1 勞動節、春節、雙十、清明、端午、中秋等）。`holidays.TW` 不可靠（5/1 在不同年份結果不同），統一走 [app/data/trading_calendar.py](app/data/trading_calendar.py)：過去日期看 `daily_price` 工作日缺資料 → 休市；今天 / 未來看 `INLINE_TWSE_HOLIDAYS` 對照表（每年 Q4 由 TWSE 公告新一年行事曆，需要人工更新）。dashboard freshness、未來新功能要算「上一個交易日 / 預期最新收盤」一律用這個模組
- 月營收**最遲次月 10 號公告**（系統 publish_date 也以 10 號 stamp，避免 look-ahead bias）；季財報 Q1=05-15、Q2=08-14、Q3=11-14、Q4=次年 03-31 公告（`financials_cumulative.publish_date` 同樣按法定下限 stamp，SQL 一律 `WHERE COALESCE(publish_date, date) <= ?` 過濾）
- 證交稅賣方依代號分流：**一般股 0.3% / 股票型 ETF 0.1% / 債券 ETF (00xxxB) 0%** — 統一走 `tax_rate_for(stock_id)` in [app/portfolio.py](app/portfolio.py)，回測引擎、持股估值、新增交易自動扣稅都吃這個 helper。手續費雙向 0.1425%（券商通常會折扣）。

## 資料源限制

- TWSE / TPEX OpenAPI：免費但只給「日 final」資料，盤後 16:30 才完整
- FinMind 免費版：`TaiwanStockFinancialStatements`、`TaiwanStockDividendResult`、`TaiwanStockSplitPrice` 三個免費 endpoint，其他付費別碰
- MOPS：POST 需帶 Referer header，IP 被 rate-limit 等幾分鐘再跑
