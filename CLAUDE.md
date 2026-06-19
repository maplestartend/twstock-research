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
**統一入口**：`stock.bat` — 互動式選單，把下列公開 .bat 收成一個選單（雙擊它即可，毋須記哪支做什麼）。它只用 `call` 調度既有 .bat、不含自己的業務邏輯，所以改功能改底層那支即可、`stock.bat` 通常不用動。
公開（仍可單獨雙擊，向後相容）：`launch` / `stop` / `restart` / `status` / `daily-update` / `install-schedule` / `uninstall-schedule` / `sync-from-cloud`（+ 別名 `pull-latest-db`）/ `check-holdings`。私有（被 call）：`_launch-servers` / `_kill-servers`。改 stop / restart 共用的殺 process 邏輯時改 `_kill-servers.bat`，兩邊都會吃到。
> 不要把 `daily-update.bat` 搬到子資料夾：`install-schedule.bat` 把它的**絕對路徑**註冊進 Windows 工作排程，搬了會讓已安裝的排程靜默失效（要重跑 install-schedule）。`stock.bat` 用「加一個入口」而非「搬檔案」來收斂雜亂，正是為了避開這個地雷。

### 7. ETF 還原價必須走 `daily_price_adj`、不要直接用 `daily_price.close`
`daily_price` 對 0050 / 00631L 跨年資料**還原模式不一致**（早期已還原、2025-06-18 1:4 split 之後是 raw post-split），直接拿來算 buy-and-hold 會誇大報酬 5×。FinMind 對槓桿 ETF 也沒提供 split events → `adj_event` 表對 00631L 是空的、`adjuster.py` 算不出。修補機制：[scripts/backfill_etf_adj_yfinance.py](scripts/backfill_etf_adj_yfinance.py) 用 yfinance `auto_adjust=True` 把 0050 / 00631L / 00692 / 00878 還原歷史灌進 `daily_price_adj`，已整合進 `daily-update.bat` 每日跑（**無條件執行**，不綁 market_update 的 RC，避免後者中途被中斷時 ETF 還原價斷層）。回測 / scoring / 過熱指標一律走 `daily_price_adj.close_adj`（或 `read_close_with_adj_coalesced` helper），不要直接讀 `daily_price.close`。
> **2026-06-20 改增量 + 修靜默失敗**：預設**增量**抓（從各 ETF 已存最後日往回 10 天），省下每天重抓 12 年。但 `auto_adjust` 在配息/分割日會**回溯重算整段歷史** → 天真增量會在新舊基準交界製造斷層；故增量會比對 overlap 區 `close_adj`，偵測到基準變動就**自動升級成 full 重抓**（保證單一一致基準）。`--full` 一次性全抓重建、`--start` 指定明確起點。**任一 ETF 硬失敗（fetch 例外 / full 抓到空）→ 回非零 exit code**，`daily-update.bat` 的 RC4 已折進總 RC（原本 script 永遠 exit 0、RC4 告警是死碼）。實測此修補當天就抓出 0050/00878/00692 已**靜默停在 2026-05-19 約一個月**沒人察覺。

### 8. `signal_history` 會無限長大，`daily-update.bat` 已每日 prune；改保留邏輯要連動清 cache
`signal_history`（~2k 列/天）每天都寫，不清就爆。[scripts/prune_signals.py](scripts/prune_signals.py) 的 `prune_all()` 把它壓成「近 365 天逐日 + 之前只留週一」，`daily-update.bat` 每天跑 `--vacuum-weekly`（每天刪列、**週日**才 best-effort VACUUM）。兩個地雷：
- **刪 `signal_history` 列會連動 `DELETE FROM factor_ic_cache`**：aggregate IC cache key 含 `COUNT(DISTINCT as_of)`，prune 後雖會自然 key-miss，但 dead row 無謂佔位且日期集合已變代表舊 IC 已過期，`prune_all()` 在「真的刪到列」時自動清（同 `backfill_signal_history.py --clear`）。
- **VACUUM 需 exclusive lock**：API server（uvicorn）開著會 SQLITE_BUSY → `--vacuum-weekly` 是 best-effort、拿不到鎖記 WARN 跳過。要就地縮小 `data/stock.db` 的完整 VACUUM 必須先 `stop.bat`（SOP 見 [docs/operations.md「歷史表保留 / VACUUM 維運」](docs/operations.md)）。VACUUM 與備份的 `VACUUM INTO`（產壓縮*副本*）是兩回事。

> **2026-06 移除 `signal_history_factor_parts`**：子因子長表（~17.5M 列、約佔全庫一半 ~2.4 GB）唯一用途是 /diagnostics 的 sub-factor IC，該功能因因子權重已穩定而下架 → 整張 `DROP TABLE` + VACUUM 回收 ~2.4 GB。aggregate factor IC + rolling IC（讀 `signal_history`）保留。同時移除績效後段班 3 個雷達策略（相對強勢 / 月營收爆發 / 外資連買）。

## 跑測試

```bash
python -m pytest tests/ -q     # 全套 535 collected（本機含 data/stock.db 才跑得動 prod-DB 整合測試）
cd web && npx tsc --noEmit     # frontend type check
```

修 `app/scoring/`、`app/backtest/`、`app/risk.py`、`api/routers/`、`app/data/adjuster.py` 之前先跑過一次。

### CI 自動化（`.github/workflows/ci.yml`）
這些關卡以前只靠人工跑，現由 GitHub Actions 在 `push: main` / PR 自動執行（硬關卡）：
- `pytest -m "not needs_prod_db"`（465 題的 DB-independent 子集）、`scripts.dump_openapi --check`（OpenAPI drift）
- 前端 `tsc --noEmit`、`scripts/check_bat.py *.bat`（BOM/CR-only=fail）、`gitleaks`（secret scan）
- SCA（`pip-audit` + `npm audit`）是**報告型**（`continue-on-error`），不阻擋。

> **`needs_prod_db` marker（`pytest.ini`）**：用 `TestClient(app)` 打實際 endpoint、吃 `data/stock.db` 的整合測試（`test_routers` / `test_backtest_router` / `test_intraday_score`）標了這個 marker。`data/stock.db`（~3.2GB）不在 git，CI 用 `-m "not needs_prod_db"` 排除；**本機跑全套**仍涵蓋它們。若新測試在 CI 因缺 prod 資料而 fail，補標 `pytestmark = pytest.mark.needs_prod_db`。
> CI 不跑 ESLint：本專案未配置 eslint（`package.json` 的 `next lint` 是 scaffold 殘留），前端關卡以 `tsc` 為準。
> 建議在 GitHub `main` 的 branch protection 把 `backend` / `frontend` / `bat-lint` / `secrets` 設為 required checks。

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
