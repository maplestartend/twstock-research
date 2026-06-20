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
| `app/data/trading_calendar.py` | 🆕 台股交易日 / 休市日判斷：DB 觀察過去工作日缺資料 = 休市；今天 / 未來查 `INLINE_TWSE_HOLIDAYS`（每年由 TWSE 公告，每年 Q4 人工更新）。dashboard freshness 用此模組計算 ok 門檻避免 5/1 勞動節 / 春節等假期被誤判為「資料過舊」 |
| `app/data/market_updater.py` | TWSE/TPEx OpenAPI 增量抓取。`fetch_one_date` 分兩階段：Phase 1 並行抓 TWSE 組與 TPEx 組（獨立 host、各自 requests.Session 各跑一條執行緒），Phase 2 序列合併+過濾權證+upsert（不並發寫 SQLite）。TWSE 的 OHLCV 與價格指數共用同一個 MI_INDEX 請求（`daily_ohlcv_and_indices` 一次抓兩表，每日少一個重複往返）→ 每交易日 7 個 HTTP（TWSE 4 + TPEx 3），主要利在回補大量歷史。`fetch_date_range` 預設 `update_progress=False`：`--date / --from..--to / --days` 這類 cherry-pick 補洞**不會**改寫 fetch_log 進度指標；只有 `update_incremental`（線性增量）才會推進。避免 `--date 2025-02-05` 把進度拖回中間某天 → 隔天 daily-update 從那裡開始爬一年多的 footgun |
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
| `stock_info` | 股票基本資料（代號、名稱、市場別、`is_tradable` 1=真股票/0=權證） | 47K（含已下市/權證；`is_tradable=1` 的真股票/ETF ~2.9K） |
| `daily_price` | 每日 OHLCV（2026-04-30 已 prune 權證 6.94M 列） | 1.47M（純真股票） |
| `institutional` | 三大法人買賣超（2026-04-30 已 prune 9.17M 列） | 1.38M |
| `margin` | 融資融券餘額 | 1.35M |
| `per_pbr` | PER/PBR/殖利率 | 1.27M |
| `financials` | 財報單季值（FinMind，僅 watchlist 股票），含 `publish_date` 法定下限 | 1.5K |
| `financials_cumulative` | 全市場 5+ 季財報累計值（TWSE/TPEX OpenAPI + MOPS 歷史，2018Q1 起 33 季） | 785K |
| `financials_quarterly_derived` | 累計差分後的單季值（含 TTM/CAGR 計算用），2018Q1 起 33 季 | 748K |
| `signal_history` | 每日雷達評分快照 | ~0.83M（近 365 天逐日 + 更早只留週一，每日 prune） |
| `factor_ic_cache` | aggregate IC 計算結果快取（key 含 `IC_ALGO_VERSION` prefix；signal_history 被 prune 時自動清空） | ~15（動態；backfill 後重建） |
| `adj_event` | 除權息/分割事件 | 每股幾筆 |
| `daily_price_adj` | 還原 OHLC（僅已補還原的股票） | ~每股千筆 |
| `index_daily` | 加權指數等（用於 RS 計算；含 yfinance 補回 2022-2023 的 ^TWII） | 37K |
| `monthly_revenue` | 月營收（2022-01 起全市場 ~1835 檔 × 50 月，已回補） | 89K |
| `holdings` | 庫存股 | 有幾檔就幾筆 |
| `trade_log` | 買賣交易紀錄 append-only | 每筆 1 列 |
| `user_weight_preset` | 權重調優頁存的命名 preset（含描述、weights JSON） | 隨用戶 |

DB 經 2026-06 移除 `signal_history_factor_parts`（子因子長表，~17.5M 列、約佔全庫一半 ~2.4 GB）後降到約 **1 GB**（更早曾因歷史快照積壓達 8.8 GB）。該表唯一用途是 /diagnostics 的 sub-factor IC 拆解，該功能因「因子權重已穩定、不再逐日檢視」而下架；aggregate factor IC + rolling IC（讀 `signal_history`，成本幾乎為零）保留。`daily-update.bat` 每日 prune `signal_history`（近 365 天逐日 + 更早只留週一）並於週日 best-effort VACUUM，避免再度膨脹。

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
   - **ETF 例外（v5d 2026-05-09）**：0050 / 00631L / 00692 / 00878 跨年資料還原不一致（混分割前/後），用
     [scripts/backfill_etf_adj_yfinance.py](../scripts/backfill_etf_adj_yfinance.py) 從 yfinance 抓
     auto_adjust=True 還原寫入 `daily_price_adj`。`daily-update.bat` 每天跑（**2026-06-20 起預設增量**：
     從已存最後日往回 10 天，偵測到配息/分割回溯重算就自動升級 full 重抓；`--full` 強制全抓重建）。
     任一 ETF 硬失敗回非零 exit code（不再靜默）。回測腳本只讀 `daily_price_adj.close_adj`，不再
     fallback 到 raw close（避免 17x 虛漲 bug）

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

5. **目前預設權重（v5b，2026-05-08；多 agent 審查後加入結構性失真保護）**
   - `COMPOSITE_WEIGHTS`：short/mid/long = **0.20 / 0.60 / 0.20**（不動 — reviewer 警告：mid IC ≈ composite IC 不足以推到 0.70/0.15/0.15）
   - `LONG_TERM_WEIGHTS`：roe/margin/eps_cagr/dividend/valuation = **0.40 / 0.20 / 0.20 / 0.10 / 0.10**（v5b：恢復 v4 原樣；v5 試做的 asset_value 子因子 cohort audit 顯示副作用 19× 治療效果而撤回，改用 ROE floor 40 in score_roe）

   **v5b 新增結構性失真保護**（不動權重，只在 sub-factor 內加分支）：
   - **B `recurring_earnings_warning`**：本業最新單季 OP < 0 且 TTM OP < 0 時，`score_roe` / `score_eps_cagr_3y` / `score_eps_growth` 切到 OperatingIncome-based core 指標。例 3708 上緯投控 2025 Q4 處分子公司 EPS 暴衝 35 → 修正後 long 從 76 → 23（避免一次性業外膨脹偽裝健康）
   - **C ROE floor 40**：5 條件 gate（PBR<0.8、debt<0.40、op>0、yield>3.5、asset_turnover<0.5）識別資產股 → ROE 子分數保底 40。例 2107 厚生 long 28.8 → 38.8。對非資產股 0 影響
   - **D 金融業 completeness cap**：金控/保險業 IFRS 報表沒 Revenue/OperatingIncome → roe + margin 雙 None，eps_cagr_3y 一手撐 80+ 分。當 industry ∈ {金融保險, 金融業} 且 long_completeness < 0.50 → cap long 在 75
   - **M1+M2 mid eps_growth 保護**：`recurring_earnings_warning` 切 OP yoy（與 long 一致）；負基期 (eps_q_prev<0) cap min(yoy, 0.5)；低基期 (|eps_q|<0.5) cap raw 75
   - **M3 建設股 revenue TTM**：industry ∈ {建材營造, 其他建材, 營造工程} → score_revenue_growth 用 revenue_ttm_yoy 取代單季（避免完工認列在 0↔95 間跳動）
   - **M4 ADV 流動性下限**：avg_volume_20 < 1M 股 → 法人 ratio 視為 0（中性 50），避免散戶為主小型股的法人雜訊放大
   - **G per_percentile guard**：< 252 日歷史 → percentile 設 None（IPO 新股不該有歷史分位）
   - **vol_ratio5 → vol_ratio20**：20 日均量比較穩定，避免昨日巨量誤判今日為弱量
   - **ETF mid None**：ETF 沒 EPS/月營收，且機構買賣多反映申購贖回非看好看壞 → mid 直接 None（仿 long）

   **v5e #1 IC 量測（2026-05-09，986 天 regime-aware composite weights）**：

   regime-aware：bull (0.20/0.60/0.20) / bear (0.20/0.45/0.35) / neutral (0.20/0.55/0.25)。
   加權指數 close vs MA200 + MA50 5 日 slope 偵測。

   | 維度 | v5c (986d) | v5e #1 (986d) | 改善 | HAC CI |
   |---|---|---|---|---|
   | composite 60d | +0.0283 IR 0.39 | **+0.0305 IR 0.42** | +8% / +8% | **[+0.0020, +0.0590] 不過 0 ✓** |
   | short 60d | +0.020 | +0.020 | 持平 | [-0.0004, +0.0397] |
   | mid 60d | +0.015 | +0.015 | 持平 | 過 0 |
   | long 60d | +0.039 | +0.039 | 持平 | 過 0 |

   改善幅度小但方向正確、跨完整 4 年 regime（2022 熊 + 2023-2024 牛 + 2025 整理 + 2026 多頭）
   驗證、HAC CI 不過 0 顯著（**統計顯著從 v5c 邊緣升到 v5e1 明確**）。regime 同時 expose 給前端
   (StockScoreView.regime)，可作為 UI 提示。

   ---

   **v5c IC 量測（2026-05-09，986 天 full backfill HAC 95% CI）— 跨 regime 驗證版**：

   | 維度 | 5d IC | 20d IC | 60d IC | 60d IR | 60d HAC CI 過 0? | vs v5b |
   |---|---|---|---|---|---|---|
   | **composite** | +0.015 | +0.024 | **+0.028** | **+0.39** | **不過 0 顯著**（CI [+0.0005, +0.0561]）| **+64%（+0.017→+0.028）**|
   | short | -0.002 | +0.011 | **+0.020** | **+0.29** | 接近顯著 [-0.0004, +0.0396] | **+263%** |
   | mid | +0.004 | +0.012 | **+0.015** | **+0.22** | 過 0 | **+206%** |
   | long | +0.033 | +0.036 | **+0.039** | **+0.42** | 過 0（regime 警報解除） | +14% |

   **重要驗證**：composite 60d HAC CI 不過 0 = **v5c 統計上顯著優於零**。240 天樣本曾出現
   long -0.035 的 regime 假警報，986 天全期收斂回 +0.039 → 確認 v5c 是穩定改善、不是 regime
   exploit。

   ---

   **v5c Wave 2 Phase 2 (2026-05-09)：4 個 Style Score 落地**

   `signal_history` 加 4 個欄位 `style_value / style_growth / style_momentum / style_income`，
   `radar.score_all` / `score_stock` 同步寫入。Style Score 用既有 sub-factor 線性加權平均、
   不影響 short/mid/long/composite 既有 IC。

   - **Value**：0.40 valuation + 0.25 dividend + 0.20 roe + 0.15 margin_quality
   - **Growth**：0.30 mid.eps_growth + 0.25 long.eps_cagr_3y + 0.20 mid.trend + 0.15 long.roe + 0.10 mid.foreign_cum，**trend < 60 時 cap 70**（修 6165 浪凡型「帳面成長但股價不漲」誤判）
   - **Momentum**：0.40 mid.trend + 0.25 short.ma_alignment + 0.15 short.volume + 0.10 short.foreign + 0.10 short.vr_macd
   - **Income**：0.50 dividend + 0.25 margin_quality + 0.15 roe + 0.10 valuation

   解決問題：long score 高 ≠ 對所有用戶都是好標的。例 6165 浪凡 composite 71.6（long 77.5）
   但 Momentum 64.1 — 動能風格用戶眼裡是中等股、不是強動能。Style Score 讓「投資風格 ≠
   引擎主排序」的用戶能在 watchlist 直接按風格 sort/filter。

   ---

   **v5c IC 量測（2026-05-08，250 天 backfill HAC 95% CI）— 早期樣本**：

   | 維度 | 5d IC | 20d IC | 60d IC | 60d IR | 60d HAC CI 過 0? | vs v5b |
   |---|---|---|---|---|---|---|
   | composite | +0.025 | +0.034 | **+0.041** | **+0.56** | 過 0 但 IR 高 | **+138%（+0.017→+0.041）**|
   | **short** | +0.004 | +0.028 | **+0.064** | **+0.88** | **不過 0 顯著** | **12x（+0.005→+0.064）**|
   | **mid** | +0.022 | +0.044 | **+0.068** | **+1.07** | **不過 0 顯著** | **13x（+0.005→+0.068）**|
   | long | +0.021 | -0.002 | -0.035 | -0.54 | 不過 0 反向 | regime 翻轉（240d 樣本期動能領先）|

   **Wave 1 改動全部驗證有效**：

   short sub-factor：
   - **kd 60d +0.068 IR 1.13 不過 0 顯著**（反向映射 work — score_kd 內 `100 - score`）
   - **ma_alignment 60d +0.085 IR 0.89 不過 0 顯著**
   - **volume 60d +0.015 IR 0.36 不過 0 顯著**（vol_ratio5 → 20d）
   - foreign 60d +0.024 IR 0.61 不過 0 顯著
   - bollinger / rsi 60d 仍反向但已降權 → v5d 處理

   mid sub-factor：
   - **trust_cum 60d +0.086 IR 3.03 不過 0 強顯著**（升權重 0.16 → 0.20）
   - **trend 60d +0.081 IR 0.99 不過 0 顯著**
   - **foreign_cum 60d +0.056 IR 1.43 不過 0 顯著**
   - revenue_growth 60d +0.002（從 v5b 的 -0.030 變雜訊 — 砍對了）
   - eps_growth 60d +0.027 IR 0.43 仍正

   long sub-factor（240 天樣本 regime 提示，非引擎問題）：
   - 240 天樣本期是「動能/成長 regime」，價值/品質因子（valuation -0.0035、margin_quality -0.052、dividend -0.026、roe -0.013、eps_cagr_3y -0.025）被動能股壓過
   - v5b 在 980 天樣本仍 +0.035 — 證明這是 regime-dependent，**非 v5c 引擎退步**
   - 後續 v5d 可加 regime detection 動態調整 long 在 composite 的權重

   **整體判讀**：v5c Wave 1 在 short/mid 維度大成功（IR 從 0.09 / 0.07 跳到 0.88 / 1.07）、composite 60d IC 翻倍。Wave 1 改動（KD 反向、margin_change 反向、volume 升權、revenue_growth 砍除、trust_cum 升權、BS 歷史 backfill）每一項都被 sub-factor IC 驗證為正確方向。

   ---

   **v5b IC 量測（2026-05-08，986 天 backfill 全期 HAC 95% CI）**：

   | 維度 | 5d IC | 20d IC | 60d IC | 60d IR | 60d HAC CI 過 0? | vs v4 變化 |
   |---|---|---|---|---|---|---|
   | composite | +0.010 | +0.017 | +0.017 | +0.24 | 過 0 | -0.034（小降但點估計穩定）|
   | short | -0.009 | -0.000 | +0.005 | +0.09 | 過 0 | 持平 |
   | mid | +0.002 | +0.007 | +0.005 | +0.07 | 過 0 | -0.023（M3 副作用）|
   | **long** | **+0.027** | **+0.030** | **+0.035** | **+0.40** | **過 0**（CI -0.008~+0.077）| **+0.007（B/C/D 治本）** |

   long sub-factor（5 因子）：
   - **valuation 60d +0.0485 IR 0.59 不過 0 顯著**（v4 +0.033 過 0；撤回 asset_value 後 valuation 訊號變強）
   - dividend 60d +0.043 IR 0.40 過 0（同產業 z-score 規一化已修偽穩定）
   - eps_cagr_3y 60d +0.020 IR 0.35 過 0（recurring_warning 修補後仍正）
   - margin_quality 60d +0.017 IR 0.24 過 0
   - roe 60d N/A（金融業 cap 後 ROE 樣本減少 — 設計意圖）

   mid sub-factor 注意點：
   - **revenue_growth 60d -0.030 IR -0.59 反向顯著** — M3 建設股切 TTM YoY 後，revenue_yoy 整體有 mean-reversion 性質。可考慮下版直接降 revenue_growth 權重 0.10 → 0.05
   - eps_growth 60d +0.012 IR 0.23 仍正（M1+M2 保護沒打掉 signal）
   - trust_cum 60d +0.020 IR 0.29 仍正

   short sub-factor 注意點：
   - **volume 60d +0.011 IR 0.28 不過 0 顯著**（vol_ratio5 → 20 的改善）
   - **kd 60d -0.028 IR -0.40 反向顯著** — 台股 KD 過熱反轉是常態，下版可考慮反向解讀或降權重
   - rsi / margin_change 也呈反向訊號

   **整體判讀**：v5b 在 **long 維度治本成功**（valuation 從雜訊→顯著、long IC IR 0.40 維持）；mid 因 M3 副作用略降但 sub-factor 結構更乾淨；composite 60d +0.017 仍統計正。撤回 asset_value 子因子（cohort audit 副作用 19× 治療效果）+ 撤回 dividend/margin 權重縮水的決策被 IC 驗證為正確 — 沒有「治了 X 病傷了 Y 路人」。

   ---

   **v4 IC 量測（2026-04-30 baseline，歷史對照）**：
     - **`eps_cagr_3y` 從 0.05 拉到 0.20**：之前以為是 data quality 問題（全 null）→ 2026-04-30 發現是 [`_fill_from_quarterly_derived`](../app/indicators/fundamentals.py) 漏算 CAGR + radar.py 財報視窗 3 年不夠 16 季 + financials 只回到 2022Q1 的三重 bug。修完並擴充 financials 到 2018Q1 後，1741 檔有 ≥16 季 EPS 可算 CAGR。**它是 long 維度裡唯一 60d HAC CI 不過 0 的因子**（IC +0.031、IR +0.45），統計顯著性最高。
     - **`margin_quality` 從 0.30 砍到 0.20**：60d IC +0.045 點估計高、但 HAC CI [-0.015, +0.106] 過 0，顯著性不足。
     - **`dividend` 從 0.15 砍到 0.10**：60d IC +0.046 但 CI [-0.009, +0.101] 過 0；獨立 reviewer 警告 dividend 跨 horizon 全 +0.035 太完美（殖利率變動慢的自相關偽穩定），HAC CI 印證警告。
     - **`roe` 不動 0.40**：5d IC +0.091 IR 2.05 為長期最強單因子（雖然只 15 dates 因為 `EquityAttributableToOwnersOfParent` 在金融保險業 OpenAPI 沒給 → ROE 只能算少數股票）。
     - **`valuation` 不動 0.10**：60d +0.033 雖 CI 過 0 但點估計穩定，保留作 regime switch 對沖。
  - `SHORT_TERM_WEIGHTS`：ma_alignment 0.18 / kd 0.10 / macd 0.04 / rsi 0.07 / bollinger 0.06 / volume 0.10 / vr_macd 0.08 / foreign 0.18 / trust 0.11 / margin_change 0.08
    - 第一版草案：因子語意改成純 VR 後，短期把量能（`volume` + `vr_macd`）上調，法人短線權重小幅下修。
  - `MID_TERM_WEIGHTS`：trend 0.32 / foreign_cum 0.20 / trust_cum 0.16 / eps_growth 0.18 / revenue_growth 0.10 / vr_macd 0.04
    - 中期仍由趨勢/基本面主導，純 VR 只做小權重輔助確認。

6. **雷達掃描不含基本面（預設）**
   - 勾「含基本面」會變慢但長期分數比較準
   - 三層財報資料源（依精確度與覆蓋度自動選擇）：
     1. `financials`（FinMind 單季，僅 watchlist）：最精準
     2. `financials_quarterly_derived`（MOPS 累計差分→單季，全市場）：有 ≥4 季就算 TTM ROE / EPS / 同期累計 YoY
     3. `financials_cumulative`（OpenAPI 當季累計，全市場最後 fallback）：只算 margin 比率
   - 全市場覆蓋率：**~1900/2295 股票有 long score**（精確比例會因每天 publish_date 限制略動）；2026-04-30 後 derived path 修補使 `eps_cagr_3y` 覆蓋從 0% 提升到 ~52%、`peg` 從 0% 提升到 ~25%。

7. **分數會是 None 代表資料不足**
   - 子指標回 None（例：新上市 < 60 日無 MA60、非 watchlist 無財報）時整個維度**跳過該項並重新歸一化剩餘權重**，不會用 50 分中性值拖平真實分數
   - 若維度可信度 < 30%，該維度直接吐 None，UI 顯示 "—"、推薦為「⚪ 資料不足」
   - 綜合分數同邏輯：短/中/長 任一為 None 就跳過並 re-normalize
   - `signal_history` 多欄位：`data_completeness`（0~1，加權後可信度）、`is_stale`（最新 daily_price 距今 > 3 天 → 1）

8. **因子實測有效性（forward-return IC）**

   用 [/diagnostics 因子檢定頁](../web/app/diagnostics/page.tsx) 對歷史快照算 cross-sectional Spearman IC。forward return 優先使用還原價（`daily_price_adj.close_adj`）以降低除權息/分割干擾。`daily_price.close=0` 視為缺值（停牌日），避免 forward return inf 污染 Q5/Q1 統計。

   **IC 95% 信賴區間：Newey-West HAC**（lag ≈ horizon-1，[`factor_diagnostics._newey_west_mean_ci`](../app/scoring/factor_diagnostics.py)）。最早版本想用 1000 iter naive bootstrap（resample per-date IC）但 forward window 重疊會造成自相關 → naive bootstrap 區間過窄。HAC 用 Bartlett kernel 修正後較保守、適合「IC 是否顯著異於 0」這類判斷。

   **IC cache 失效規則**：cache key = `({IC_ALGO_VERSION}:{MAX(signal_history.as_of)}, scope, lookback_days)`。改演算法（factor 公式、forward return 邏輯、HAC lag、quintile 切法）時 bump [`factor_diagnostics.IC_ALGO_VERSION`](../app/scoring/factor_diagnostics.py)；資料變動時 `MAX(as_of)` 自然推進、不需要 bump。

   ### v4 weights @ 980-day baseline（2026-04-30 量測，純 VR 因子 + bug 修補後 R3 backfill）

   2026-04-30 一次性修補 3 條 bug 鏈讓 `eps_cagr_3y` 真正能算（之前 derived path 全 null）：
   1. `_fill_from_quarterly_derived` 漏算 CAGR / PEG / 單季 YoY
   2. radar.py bulk path 財報視窗 3 年 → 5 年（CAGR 需 16 季 = 4 年）
   3. `financials_quarterly_derived` 由 17 季回補到 33 季（2018Q1 起，覆蓋 backfill 全期）

   結果：`eps_cagr_3y` 在 factor_parts 從 0% 非 null → 52% 非 null（1741 檔有 ≥16 季 EPS）。

   #### Aggregate IC（forward return 走還原價，HAC 95% CI）

   | 因子 | 5 日 IC [CI] | 20 日 IC [CI] | 60 日 IC [CI] | n | 結論 |
   |---|---|---|---|---|---|
   | 綜合 | +0.031 [+0.016, +0.046] | +0.053 [+0.021, +0.084] | +0.051 [+0.016, +0.086] | 713/698/658 | **三個 horizon CI 都不過 0** — 統計上顯著正向 |
   | 中期 | +0.020 | +0.036 | +0.028 [-0.015, +0.071] | 同上 | 60d CI 過 0 但點估計穩定 |
   | 長期 | +0.032 [+0.020, +0.044] | +0.044 [+0.017, +0.071] | +0.055 [+0.002, +0.108] | 同上 | **三個 horizon CI 都不過 0** — 與 v3 相比 60d 略降但 CI 收緊 |
   | 短期 | -0.005 | +0.005 | +0.016 [+0.001, +0.032] | 同上 | 5d/20d 接近 0、60d 才有顯著正 |
   | 純 VR | -0.008 [-0.014, -0.001] | -0.015 [-0.028, -0.002] | -0.015 [-0.033, +0.003] | 同上 | 5d/20d 反向顯著（量爆 → 均值回歸）；60d CI 過 0 |

   #### Long sub-factor IC（v4 權重決策的依據）

   | factor | 5d | 20d | 60d | IR(60d) | CI(60d) 過 0? |
   |---|---|---|---|---|---|
   | dividend | +0.038 | +0.038 | +0.046 | +0.40 | ✓ [-0.009, +0.101] |
   | **eps_cagr_3y** | **+0.017** | **+0.030** | **+0.031** | **+0.45** | **✗ [+0.004, +0.058]（唯一顯著）** |
   | margin_quality | +0.023 | +0.034 | +0.045 | +0.37 | ✓ |
   | valuation | +0.021 | +0.023 | +0.033 | +0.41 | ✓ |
   | roe | +0.091 | — | — | +2.05 | ⚠ 只 15 dates 稀少（金融保險業缺 equity） |

   ### v4 權重決策（2026-04-30）：**改 LONG_TERM_WEIGHTS、保留 SHORT/MID/COMPOSITE**

   - `roe` 0.40 → 0.40（不動，5d 最強單因子但稀少）
   - `margin_quality` 0.30 → **0.20**（CI 過 0、顯著性不足）
   - `eps_cagr_3y` 0.05 → **0.20**（bug 修完是 long 裡唯一 60d CI 不過 0 的因子）
   - `dividend` 0.15 → **0.10**（CI 過 0 + reviewer 警告 yield 自相關偽穩定）
   - `valuation` 0.10 → 0.10（不動）

   v4 vs v3 weights 結果（兩者都是 980 天 backfill）：
   - composite 60d IC: +0.052 → +0.051（-0.001，不動如山）
   - long 60d IC: +0.060 → +0.055（-0.005 點估計微降，但 CI 從 [-0.002, +0.123] 收緊到 [+0.002, +0.108] — **從 borderline 變顯著**）

   **解讀**：v4 沒換到更高 in-sample IC，但統計可信度提升（用唯一 60d 顯著的 eps_cagr_3y 取代 CI 過 0 的 margin/dividend）。在現有 980 天樣本（有效獨立 N≈15）下，這是「robustness over point estimate」的選擇。

   #### Short / Mid sub-factor 觀察

   - **mid trend** 60d +0.041 — 中期最強單因子
   - **short ma_alignment** 60d +0.046 [CI +0.008, +0.085] — 短期唯一顯著正向
   - **short kd** 60d -0.041、**short rsi** 60d -0.046、**short bollinger** 60d -0.040 — 反向訊號穩定（量價超買 → 均值回歸）
   - **mid revenue_growth** 60d -0.038 [CI -0.072, -0.003] — 反向顯著（短線追業績的散戶逆向指標？）
   - **mid eps_growth** 60d +0.009、**mid foreign_cum** 60d +0.016 — 不顯著

   ### 重新跑此分析的方法

   ```bash
   # 改 scoring 後跑（清舊算法 + 重算 + 預熱 IC cache）
   python -m scripts.backfill_signal_history --days 100 --clear --workers 6

   # 全期回放（i5-13400F + workers=6 約 100 分鐘）
   python -m scripts.backfill_signal_history --days 1000 --clear --workers 6
   ```

   ### 樣本量對結果的可信度警告

   60d horizon 在 980 天樣本下 ~658 個 IC 點，但這些 forward window 互相高度重疊（有效獨立樣本 ≈ 920 / 60 ≈ 15）。HAC CI 已修正自相關，但 N=15 仍是真實統計門檻 — 點估計 +0.055 看起來不錯但 t-stat 仍接近顯著性邊界。**繼續調權重前建議先補 yfinance 個股 OHLC（目前 2022-2023 早期段只 ~100 watchlist 檔評分），讓全期樣本一致**。

   ### 歷史 baselines（已 deprecated，僅供對照）

   早期版本（v2/v3 weights、舊版 VR×MACD 因子、100 天樣本）已棄用。當時數字：composite 60d 從 +0.146 縮到 +0.038 (980 天)，long 60d 從 -0.023 翻正到 +0.032 — 但都基於有 bug 的 `eps_cagr_3y` (全 null)。git log 可查 commit `95726ce`、`3ad737d`、`843546b` 各版本的數字。

### ⚠️ 程式面

1. **後端改 Python 模組需留意重載**
   - uvicorn `--reload` 在 Windows 偶有偵測失靈，改了 `app/scoring/*.py` 後若行為不對，FastAPI 視窗按 `Ctrl+C` 重起
   - 前端改 `.tsx` 走 HMR 自動熱重載

2. **快取有兩層**
   - Next.js Server Components 預設 `revalidate = 0`（不快取），每次 request 都重新打 API
   - 雷達有磁碟快取 `data/cache/radar_*.parquet`，以「日線最新日 + 月營收最新日」為 key，重啟服務也秒開
   - 跑完 `market_update`（日期推進）或 `--mops`（月營收更新）後快取會自動失效
   - 想強制清：雷達頁的「🔁 重新掃描」按鈕

3. **score_all / 回填效能優化（2026-04）**
   - `score_all()` 新增 `candidate_stocks` 參數，`backfill_signal_history` 會預先算一次候選池並重用，避免每天重掃 `daily_price`
   - 批次評分改用 `technical.enrich_for_scoring()`（只算評分會用到的指標），減少 DataFrame 寫入成本
   - 資料讀取改分窗：技術（300 天）、籌碼（120 天）、估值（400 天）以降低 I/O
   - 實測（2026-04-29）：完整快照約 **58 秒/天**；`--no-fundamentals` 約 **42 秒/天**
   - **多進程並行（2026-06-20）**：per-stock 評分是 GIL-bound（pandas/Python 為主），執行緒實測反而更慢（0.76x）→ 改用「多進程切塊」：全市場快照（無 stock_ids/live_prices、≥200 檔、且自己不在子進程內）自動把候選股切成 N 塊、各進程用 `stock_ids=chunk` 跑序列 score_all、rows 合併後一次建 DataFrame。實測 **54s → 14s（~4x）**、輸出與序列 **bit-identical（含 dtype）**。worker 數 `min(8, cpu)`；`max_workers` 可強制（1=序列）。**nested 防護**：`backfill_signal_history` 已用 ProcessPool 跨日並行（day-worker 是子進程），score_all 偵測 `mp.parent_process()` 非 None 時退序列，避免 4×8=32 進程過度訂閱。守門：[tests/test_score_all_parallel.py](../tests/test_score_all_parallel.py)

4. **資料庫已啟用 WAL 模式**
   - SQLite 讀寫可並行，UI 跑的同時 `market_update` 寫入不會卡
   - 但仍建議不要同時開多個 FastAPI 實例

5. **分數來源（雷達/自選/持股 vs 詳情頁）**
   - 列表頁讀 `signal_history` 最新一筆當「當下分數」（速度考量）
   - 詳情頁仍即時跑 `score_stock`
   - [snapshot_freshness.ensure_fresh()](../app/scoring/snapshot_freshness.py) 在列表 API 開頭比對 snapshot.as_of vs daily_price.MAX(date)，落後就阻塞補跑（with lock，併發只跑一次）
   - 歷史追蹤頁與分數走勢折線圖讀「歷史快照」（回測用途，不受影響）

6. **盤中即時 / what-if 重算**
   - 詳情頁 `StockScorePanel` 提供「收盤 / 即時 / 假設」三模式切換（[web/app/stocks/\[stockId\]/StockScorePanel.tsx](../web/app/stocks/[stockId]/StockScorePanel.tsx)）
   - 即時：抓 mis.twse.com.tw 盤中報價 → `score_stock(live_price=...)` 覆寫最新一筆 close 重算技術面
   - 假設：使用者輸入價位（±10% 滑桿）→ 同樣走 `live_price` 路徑
   - 短/中分數會跟著動，**長期分數固定不動**（吃 ROE/EPS/股利等財報指標，盤中價無關）
   - **不寫入 `signal_history`** — 重算只服務 UI 互動，回測來源仍是收盤後 snapshot（避免 look-ahead bias）
   - mis client：[app/data/intraday.py](../app/data/intraday.py)，30 秒記憶體快取避免 hammer；興櫃 / 休市 / mis 異常 → 422，前端隱藏「即時」按鈕
