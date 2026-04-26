# 架構與設計筆記

> 給開發者 / agent 對照用。日常使用見 [../USAGE.md](../USAGE.md)。

## 技術棧 & 目錄

| 目錄 | 角色 |
|------|------|
| `api/` | FastAPI 薄殼，把既有 `app.*` 模組包裝成 REST endpoints（schemas + routers） |
| `api/common.py` | router 層共用 helper（safe_float、fmt_date、make_placeholders、get_stock_name(s)） |
| `api/schemas/common.py` | Pydantic 基類：CamelModel、StockRef、StockRefOptional |
| `app/scoring/radar_queries.py` | dashboard ↔ radar 共用的 signal_history 查詢層 |
| `app/scoring/radar_cache.py` | radar 命中清單 parquet cache（key=`MAX(monthly_revenue.date)+MAX(daily_price.date)`） |
| `app/scoring/market_scope_cache.py` | 🆕 industry_rotation + market_breadth parquet/JSON cache（key=`MAX(daily_price.date)`，每日只算一次） |
| `app/scoring/snapshot_freshness.py` | 列表 API 開頭呼叫 `ensure_fresh()`，snapshot 落後 daily_price 時自動補跑（with lock） |
| `web/` | Next.js 15 App Router，TypeScript，Tailwind v4，自家 design tokens |
| `web/components/primitives/` | 通用 UI：PageHeader、EmptyState、Field、Th/Td、🆕 TableContainer（Th + bg + overflow + 內建 ScrollHint）、🆕 StockIdCell（13 個 listing 表共用「代號/名稱」cell）、Pagination、Icon、KPIStat、ScoreBadge、PriceCell、ThemeToggle、RiskAlertList、SnapshotFreshnessIndicator、SnapshotDeltaPanel、TableScrollHint（Taiwan 漲紅跌綠硬編進 token） |
| `web/components/charts/` | CandlestickChart（lightweight-charts，陽紅陰綠）+ ScoreTimelineChart / BacktestEquityChart（Recharts）+ IndustryHeatmap（d3-hierarchy 自繪 SVG） |
| `web/lib/` | API 封裝（api.ts）、共用樣式 class（formClasses.ts）、回測情景預設（scenarios.ts）、評分子項中文標籤（labels.ts）、詞彙表（terms.ts） |

## API Docs

- Swagger UI：`http://localhost:8000/docs`
- OpenAPI JSON：`http://localhost:8000/openapi.json`（未來可用 `openapi-typescript` 自動產前端 TS 型別）
- 完整 API 規格請見 [api-spec.md](api-spec.md)
- 完整前端規格請見 [frontend-spec.md](frontend-spec.md)

## 主題切換

右上角 segmented control：**淺色 / 跟隨系統 / 深色**。選「跟隨系統」時會即時跟 OS 切換。
所有設計 token（漲跌色、分數色階、表面色…）亮暗兩套都已對過 WCAG AA 對比度。

## Design tokens 結構（[web/styles/tokens.css](../web/styles/tokens.css)）

採 **Primitive → Semantic** 兩層：
- **Primitive**：`--up-50~900`、`--down-50~900`、`--brand-50~900`、`--neutral-50~950`、`--info-500`、`--warning-500`、`--error-500`。元件層**禁用**。
- **Semantic**：元件層唯一允許的入口。涵蓋：
  - 表面：`--bg-canvas`、`--bg-surface`、`--bg-subtle`、`--bg-muted`
  - 邊框：`--border-default`、`--border-strong`
  - 文字：`--text-primary`、`--text-secondary`、`--text-tertiary`、`--text-disabled`、`--text-inverse`
  - 漲跌：`--color-up`、`--color-up-bg`、`--color-up-border`、`--color-down`、`--color-down-bg`、`--color-down-border`、`--color-flat`
  - 分數五階：`--score-{strong-pos,pos,neutral,caution,danger}-{bg,fg}`
  - 推薦：`--reco-{buy,hold,sell}-{bg,fg}`
  - 提示面板：`--info-{bg,fg,border}`、`--warning-{bg,fg,border}`、`--error-{bg,fg,border}`
  - **Tooltip**（含 InfoTip 與圖表 tooltip 共用）：`--tooltip-{bg,fg,border}` ；`--chart-tooltip-bg/-fg` 從這同源
  - **Chart 系列**：`--chart-series-{short,mid,long,composite}`、`--chart-ma{20,60}`、`--chart-grid`、`--chart-axis`
  - Focus ring：`--focus-ring`

> **加新顏色的規則**：先想「它的語意是什麼」？落到既有語意（成功/警告/分數階…）就用既有 token；新類別才加新 semantic token，並同時在 light + dark 兩個 block 補齊。**不要在元件 inline `#xxx`**。

> Recharts（SVG）可直接 `stroke="var(--chart-series-short)"`；lightweight-charts（canvas）要用 `getComputedStyle().getPropertyValue('--xxx')` 動態抓 — 範例見 [CandlestickChart.tsx](../web/components/charts/CandlestickChart.tsx)。

## 資料庫結構

位置：`data/stock.db` (SQLite，啟用 WAL)

| 表 | 用途 | 筆數量級 |
|---|------|---------|
| `stock_info` | 股票基本資料（代號、名稱、市場別） | 25K |
| `daily_price` | 每日 OHLCV | 200 萬+ |
| `institutional` | 三大法人買賣超 | 280 萬+ |
| `margin` | 融資融券餘額 | 44 萬 |
| `per_pbr` | PER/PBR/殖利率 | 40 萬 |
| `financials` | 財報單季值（FinMind，僅 watchlist 股票） | 1.5K |
| `financials_cumulative` | 全市場 5+ 季財報累計值（TWSE/TPEX OpenAPI + MOPS 歷史） | 130K |
| `financials_quarterly_derived` | 累計差分後的單季值（含 TTM 計算用），全市場 ~1900 檔 | 100K |
| `signal_history` | 每日雷達評分快照 | 增長中 |
| `adj_event` | 除權息/分割事件 | 每股幾筆 |
| `daily_price_adj` | 還原 OHLC（僅已補還原的股票） | ~每股千筆 |
| `index_daily` | 加權指數等（用於 RS 計算） | 每日 50+ 筆 |
| `monthly_revenue` | 月營收（2022-01 起全市場 ~1835 檔 × 50 月，已回補） | 89,000+ |
| `holdings` | 庫存股 | 有幾檔就幾筆 |
| `trade_log` | 買賣交易紀錄 append-only | 每筆 1 列 |
| `user_weight_preset` | 權重調優頁存的命名 preset（含描述、weights JSON） | 隨用戶 |

DB 目前約 **460 MB**，預計每年增長 100~150 MB。

## 注意事項與限制

### ⚠️ 資料面

1. **FinMind token 是半私人的**
   - 已放在 `config.yaml`，已加入 `.gitignore`
   - 也支援 `FINMIND_TOKEN` 環境變數覆寫（優先度高於 yaml）
   - 要 git push 前再三確認 `config.yaml` 沒跟著上去

2. **FinMind 免費版額度有限**
   - 約 600 requests/hour
   - 所以 `update_adj --all-in-db` 對全市場 2700 檔要分批跑、或乾脆不跑
   - FinMind 免費版也不支援不帶 `data_id` 的 bulk 模式（會回 "Your level is register"），月營收全市場改走 `--mops` 的 TWSE/TPEX OpenAPI

3. **還原價只處理自選股**
   - 雷達/短期分數不用還原價（影響微小）
   - 回測會用還原價（如果有）
   - 個股詳情 K 線圖可勾選「使用還原價」

4. **ROE 計算優先序**
   - 優先 `financials_cumulative` 的 `EquityAttributableToOwnersOfParent`（TWSE OpenAPI 期末餘額，最準確）
   - 退回 FinMind 同欄位作 fallback（語意可能不準，作備用）
   - sanity check：>60% 視為異常忽略
   - 金控/保險業 OpenAPI 沒給 equity 欄位 → ROE 仍會 None（合理，業別不同）

5. **月營收 / 季財報資料來源**
   - **最新月全市場**：TWSE/TPEX OpenAPI，`update_monthly_revenue --mops`，~2 秒
   - **歷史月全市場**：MOPS 舊版備用域 `mopsov.twse.com.tw`，`--mops --from 2022-01 --to 2026-03`，51 個月約 1 分鐘
   - **最新季全市場**：TWSE/TPEX OpenAPI 的 `t187ap06_*`（綜損）+ `t187ap07_*`（資產負債），`update_financials_mops`，~10 秒
   - **歷史季全市場**：MOPS `ajax_t163sb04` POST，`backfill_financials_history`，~3 秒/季
   - **FinMind 逐檔**：`daily_update --stock 2330` / `backfill_monthly_revenue` 是備用路徑
   - 累計值寫入 `financials_cumulative`，由 `derive_quarterly_from_cumulative()` 差分產生 `financials_quarterly_derived`（單季值）；與 FinMind 單季差分對齊誤差 0.00%

6. **Discord Webhook URL 也是敏感資訊**
   - 拿到 URL 的人都能往你的頻道發訊息
   - 若擔心洩漏，刪舊 webhook 重建一個即可
   - 支援 `DISCORD_WEBHOOK_URL` 環境變數覆寫

### ⚠️ 策略面

1. **ETF 與個股的評分機制不同**
   - ETF 沒有 EPS / ROE / 月營收，**長期分數固定為 None**（不是 re-normalize 出來的代用值）
   - 因此 ETF 的綜合分數只用「短期 + 中期」加權算（completeness 反映此事實）
   - 雷達 / 自選股總覽都把 ETF 與個股分到不同 tab

2. **評分使用還原價**
   - score_stock / score_all 一律 LEFT JOIN `daily_price_adj`，技術指標走還原 OHLC
   - 沒還原資料的股票自動 fallback 原始價（含 fillna 處理）
   - 想補還原價：`python -m scripts.update_adj`

3. **評分不是買賣訊號**
   - 是「研究起點」不是「自動交易訊號」
   - 回測 2 年來多數股票 Alpha 為負（規則太僵、錯過漲勢）

4. **短線強勢門檻偏嚴**
   - 進場 65 分、出場 40 分，很多行情會錯過
   - 建議用權重調優頁找出符合自己直覺的參數

5. **雷達掃描不含基本面（預設）**
   - 勾「含基本面」會變慢但長期分數比較準
   - 三層財報資料源（依精確度與覆蓋度自動選擇）：
     1. `financials`（FinMind 單季，僅 watchlist）：最精準
     2. `financials_quarterly_derived`（MOPS 累計差分→單季，全市場）：有 ≥4 季就算 TTM ROE / EPS / 同期累計 YoY
     3. `financials_cumulative`（OpenAPI 當季累計，全市場最後 fallback）：只算 margin 比率
   - 全市場覆蓋率：**1939/2291 (84.6%) 股票有 long score**，平均 data_completeness 0.885

6. **分數會是 None 代表資料不足**
   - 子指標回 None（例：新上市 < 60 日無 MA60、非 watchlist 無財報）時整個維度**跳過該項並重新歸一化剩餘權重**，不會用 50 分中性值拖平真實分數
   - 若維度可信度 < 30%，該維度直接吐 None，UI 顯示 "—"、推薦為「⚪ 資料不足」
   - 綜合分數同邏輯：短/中/長 任一為 None 就跳過並 re-normalize
   - `signal_history` 多欄位：`data_completeness`（0~1，加權後可信度）、`is_stale`（最新 daily_price 距今 > 3 天 → 1）

### ⚠️ 程式面

1. **後端改 Python 模組需留意重載**
   - uvicorn `--reload` 在 Windows 偶有偵測失靈，改了 `app/scoring/*.py` 後若行為不對，FastAPI 視窗按 `Ctrl+C` 重起
   - 前端改 `.tsx` 走 HMR 自動熱重載

2. **快取有兩層**
   - Next.js Server Components 預設 `revalidate = 0`（不快取），每次 request 都重新打 API
   - 雷達有磁碟快取 `data/cache/radar_*.parquet`，以「日線最新日 + 月營收最新日」為 key，重啟服務也秒開
   - 跑完 `market_update`（日期推進）或 `--mops`（月營收更新）後快取會自動失效
   - 想強制清：雷達頁的「🔁 重新掃描」按鈕

3. **資料庫已啟用 WAL 模式**
   - SQLite 讀寫可並行，UI 跑的同時 `market_update` 寫入不會卡
   - 但仍建議不要同時開多個 FastAPI 實例

4. **分數來源（雷達/自選/持股 vs 詳情頁）**
   - 列表頁讀 `signal_history` 最新一筆當「當下分數」（速度考量）
   - 詳情頁仍即時跑 `score_stock`
   - [snapshot_freshness.ensure_fresh()](../app/scoring/snapshot_freshness.py) 在列表 API 開頭比對 snapshot.as_of vs daily_price.MAX(date)，落後就阻塞補跑（with lock，併發只跑一次）
   - 歷史追蹤頁與分數走勢折線圖讀「歷史快照」（回測用途，不受影響）
