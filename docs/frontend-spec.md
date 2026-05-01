# 前端規格

> 自動產出於 2026-04-26。新增/修改頁面或元件時請同步更新本檔。
> 與 [docs/api-spec.md](api-spec.md) 搭配閱讀。

## 全域慣例

- **框架**：Next.js 15 App Router，預設 RSC，需要互動才 `"use client"`
- **TypeScript** + **Tailwind v4**（CSS variables / design tokens）
- **API 慣例**：camelCase（後端 FastAPI 已用 `alias_generator = to_camel`）
- **API client**：[web/lib/api.ts](../web/lib/api.ts)（封裝 fetch）；type alias 與後端 Pydantic 1:1
- **表單樣式 class** 集中於 [web/lib/formClasses.ts](../web/lib/formClasses.ts)：
  - `inputCls`：標準輸入框（含 focus ring）
  - `rangeCls`：滑桿（accent 色 = brand-500）
  - `btnPrimary`：藍底主按鈕（h-10）
  - `btnSecondary`：灰邊次按鈕（h-10）
  - `btnDestructive`：紅底刪除按鈕（h-9）
- **共用表格 primitives**（[web/components/primitives/](../web/components/primitives/)）
  - **`Table.tsx`** — `Th`（h-10，預設 12px tracking-wide secondary 色）、`Td`（h-14 comfortable / h-12 compact，由 `size` 切換）
  - **`TableContainer.tsx`** — `rounded-xl + border + bg-surface + overflow-x-auto`，內含 `<TableScrollHint>`（mobile 右側 chevron 提示可橫向捲動）
  - **`StockIdCell.tsx`** — 「代號 / 名稱」cell，代號 numeric semibold + primary 色，名稱 12px secondary。13 處 listing 表共用
  - listing 表（雷達/自選/持股/行事曆/歷史）用 `size="comfortable"`（h-14，預設）
  - 結果表 / 工具表（回測明細、grid、DQ、watchlist-manage）用 `size="compact"`（h-12）
- **欄寬穩定**：分頁間欄寬要一致 → `table-fixed` + 顯式 `<Th className="w-[NNpx]">`
- **表格字級**：base `text-[15px]` + Th 自帶 12px header；分數 / 建議欄 `align="center"`，數字欄 `align="right"`
- **頁面 hierarchy**：page H1（PageHeader）24px / 600；section H2（SectionTitle）16px / 600；KPI primary 22-28px tabular-nums；body 15px；secondary chip 11px floor
- **台股慣例配色**：漲紅跌綠（`--color-up` = 紅、`--color-down` = 綠）
- **錯誤處理**：所有頁面 RSC `try / catch` 後 `<BackendDownError>`；表單錯誤走 `humanizeApiError(rawMsg)`
- **API base URL**：`process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000"`，dev 時 `next.config.mjs` 把 `/api/*` rewrite 到後端

---

## 頁面總覽

| Path | File | RSC/Client | 主要 API |
|---|---|---|---|
| `/` | [web/app/page.tsx](../web/app/page.tsx) | RSC | `/api/portfolio/*`, `/api/dashboard/*`, `/api/watchlist/movers` |
| `/stocks/[stockId]` | [web/app/stocks/[stockId]/page.tsx](../web/app/stocks/[stockId]/page.tsx) | RSC | `/api/stocks/{id}/{meta,score,price,score-history}` |
| `/radar` | [web/app/radar/page.tsx](../web/app/radar/page.tsx) | RSC | `/api/radar/{strategies,hits}` |
| `/history` | [web/app/history/page.tsx](../web/app/history/page.tsx) | RSC | `/api/history/{dates,strategies,performance}` |
| `/holdings` | [web/app/holdings/page.tsx](../web/app/holdings/page.tsx) | RSC + 1 client child | `/api/portfolio/{summary,holdings,risk-alerts,trades,realized-pnl}` |
| `/watchlist` | [web/app/watchlist/page.tsx](../web/app/watchlist/page.tsx) | RSC | `/api/watchlist/overview` |
| `/watchlist-manage` | [web/app/watchlist-manage/page.tsx](../web/app/watchlist-manage/page.tsx) + [client.tsx](../web/app/watchlist-manage/client.tsx) | RSC + Client | `/api/watchlist`, `/api/watchlist/{lookup,bulk-add,bulk-remove}` |
| `/dq` | [web/app/dq/page.tsx](../web/app/dq/page.tsx) | RSC | `/api/dq/summary`, `/api/dashboard/data-freshness` |
| `/sectors` | [web/app/sectors/page.tsx](../web/app/sectors/page.tsx) | RSC + Client child | `/api/market/{breadth,industry-rotation,industry-members}` |
| `/dividend-calendar` | [web/app/dividend-calendar/page.tsx](../web/app/dividend-calendar/page.tsx) | RSC | `/api/calendar/ex-dividend` |
| `/backtest` | [web/app/backtest/page.tsx](../web/app/backtest/page.tsx) | RSC | `/api/watchlist`, POST `/api/backtest/stock` |
| `/portfolio-backtest` | [web/app/portfolio-backtest/page.tsx](../web/app/portfolio-backtest/page.tsx) | RSC | `/api/watchlist`, POST `/api/backtest/portfolio` |
| `/event-backtest` | [web/app/event-backtest/page.tsx](../web/app/event-backtest/page.tsx) | RSC | `/api/watchlist`, POST `/api/backtest/event-driven` |
| `/grid-search` | [web/app/grid-search/page.tsx](../web/app/grid-search/page.tsx) | RSC | `/api/watchlist`, POST `/api/backtest/{grid-search,walk-forward}` |
| `/weight-tuner` | [web/app/weight-tuner/page.tsx](../web/app/weight-tuner/page.tsx) + [client.tsx](../web/app/weight-tuner/client.tsx) | RSC + Client | `/api/weight-tuner/{breakdown,presets,presets/visible-keys}` |

Root layout：[web/app/layout.tsx](../web/app/layout.tsx) — 套上 `<Sidebar>`、`<Topbar>`、`<CommandPalette>`、preTheme script（避免 FOUC）。

---

## 各頁面詳述

### `/` — 今日戰情室（dashboard）

- **檔案**：[web/app/page.tsx](../web/app/page.tsx)（227 行）
- **類型**：RSC（`revalidate: 60`）
- **API 呼叫**：
  - `apiGet`：`/api/portfolio/summary`、`/api/portfolio/holdings`、`/api/portfolio/risk-alerts`、`/api/dashboard/radar-hits?limit=8`、`/api/dashboard/ex-dividend?days_ahead=7`、`/api/dashboard/data-freshness`
  - `apiGetOptional`：`/api/watchlist/movers?top=5&direction=up|down`
- **主要 sections**：
  - PageHeader（line 50）
  - KPI row × 5（line 53）：總市值/今日損益/未實現/雷達命中/資料狀態
  - 持股快照 + 風險提醒（line 97）
  - 今日雷達命中側欄（line 114）
  - 自選漲幅榜 / 跌幅榜 / 近 7 日除權息（line 145）
  - 各表新鮮度 footer（line 161）
- **用到 components**：`KPIStat`、`HoldingsTable`、`RadarHitChip`、`RiskAlertList`、`DataFreshnessBadge`、`PriceCell`、`PageHeader`、`EmptyState`、`BackendDownError`

### `/stocks/[stockId]` — 個股詳情

- **檔案**：[web/app/stocks/[stockId]/page.tsx](../web/app/stocks/[stockId]/page.tsx)（263 行）
- **類型**：RSC（`revalidate: 60`），動態路由
- **API 呼叫**：全 `apiGetOptional`
  - `/api/stocks/{id}/meta`、`/api/stocks/{id}/score`、`/api/stocks/{id}/price?days=180`、`/api/stocks/{id}/score-history?days=90`、**🆕** `/api/stocks/{id}/peers`（同業比較區塊；ETF/興櫃/樣本不足回 404 → 整段隱藏）
- **主要 sections**：
  - StockHeader：代號 + 名稱 + PriceCell expanded
  - StockScorePanel（client）：模式切換（收盤/即時/假設）+ 5-KPI row + 評分拆解 + 進出場建議
    - **🆕** loading 時 KPI/Breakdown 卡降透明度 (`opacity-60` + `aria-busy`)
  - NarrativeSection（client）：AI 解讀（on-demand）
  - **🆕** PeerComparisonSection（[PeerComparisonSection.tsx](../web/app/stocks/[stockId]/PeerComparisonSection.tsx)）：7 列 horizontal bar pair（value vs 同業中位數）+ #rank/N 徽章；單列 unit="%" → 0.xx 自動轉百分比，unit="倍" → `2.62×`
  - K 線：`<CandlestickChart>` 含 MA20/MA60 + 成交量
  - ATR 動態出場 + 部位試算
  - 分數走勢：`<ScoreTimelineChart>`
- **fallback**：meta 但無 price → 顯示「資料不足」訊息；有 price 無 score → 顯示「尚未產生快照」
- **用到 components**：`PriceCell`、`ScoreBadge`、`RecommendationTag`、`ScoreBreakdownBars`、`Icon`、`CandlestickChart`、`ScoreTimelineChart`

### `/radar` — 雷達掃描

- **檔案**：[web/app/radar/page.tsx](../web/app/radar/page.tsx)（354 行）
- **類型**：RSC（`revalidate: 60`）
- **searchParams**：`strategy, market[], top (30/50/100/all), type (stock/etf), page`
- **API 呼叫**：`apiGet<RadarStrategy[]>("/api/radar/strategies")`、`apiGet<RadarHit[]>("/api/radar/hits?...")`
- **主要 sections**：
  - PageHeader
  - Type tabs：個股 / ETF —— ETF 過濾掉 `stocksOnly` 策略
  - Strategy chips 含命中數
  - Filters：板別（上市/上櫃）+ Top N 切換
  - Hits table 含 client-side 50/頁分頁、`<Pagination>`，標題列右側 `<DownloadCsvButton>` + **🆕** `<DownloadXlsxButton href="/api/radar/export.xlsx?strategy=...&market=...">`（保留當前過濾條件）
- **欄寬**：`table-fixed`，固定欄寬；ETF tab 隱藏「長期」欄
- **用到 components**：`Icon`、`PageHeader`、`EmptyState`、`ScoreBadge`、`RecommendationTag`、`PriceCell`、`BackendDownError`、`Th/Td`、`Pagination`、`DownloadCsvButton`、`DownloadXlsxButton`

### `/history` — 歷史追蹤

- **檔案**：[web/app/history/page.tsx](../web/app/history/page.tsx)（336 行）
- **類型**：RSC（`revalidate: 300`）
- **searchParams**：`as_of, strategy, type (stock/etf), page`
- **API 呼叫**：`apiGet<string[]>("/api/history/dates")`、`apiGet<RadarStrategy[]>("/api/history/strategies?...")`、`apiGetOptional<HistoryPerfSummary>("/api/history/performance?...")`
- **主要 sections**：
  - PageHeader（line 302）
  - 快照日期 chip（line 109）顯示前 10 個交易日
  - Type tabs：個股 / ETF（line 140）
  - Strategy chip 列表（line 165）
  - KPI row × 4（line 189）：命中檔數/勝率/平均漲幅/經過天數
  - 命中表現表（line 210）含 client-side 50/頁分頁
- **用到 components**：`PageHeader`、`EmptyState`、`ScoreBadge`、`RecommendationTag`、`PriceCell`、`KPIStat`、`Th/Td`、`Pagination`

### `/holdings` — 我的持股

- **檔案**：[web/app/holdings/page.tsx](../web/app/holdings/page.tsx)（200 行）+ [TradesPanel.tsx](../web/app/holdings/TradesPanel.tsx)（client child，新增/刪除交易）
- **類型**：RSC（`revalidate: 60`），含 1 個 `"use client"` child
- **API 呼叫**：
  - RSC：`/api/portfolio/{summary,holdings,risk-alerts,trades?limit=50,realized-pnl}`
  - Client（TradesPanel）：POST `/api/portfolio/trades`、DELETE `/api/portfolio/trades/{id}`
- **主要 sections**：
  - PageHeader（line 41）
  - KPI row × 4（line 48）：持股檔數/成本/市值/未實現損益
  - 持股明細 `<HoldingsTable>`，標題列右側 **🆕** `<DownloadXlsxButton href="/api/portfolio/holdings/export.xlsx">`
  - 風險提醒卡
  - 已實現損益 含 mini KPI + 配對表
  - `<TradesPanel>`：新增交易表單 + 最近 50 筆交易刪除
- **用到 components**：`PageHeader`、`EmptyState`、`KPIStat`、`HoldingsTable`、`RiskAlertList`、`DownloadXlsxButton`、`Th/Td`、`Icon`

### `/watchlist` — 自選股總覽

- **檔案**：[web/app/watchlist/page.tsx](../web/app/watchlist/page.tsx)
- **類型**：RSC（`revalidate: 60`）
- **searchParams**：`type (stock/etf)`、**🆕** `tag`（過濾僅顯示帶該 tag 的檔；切 type tab 時保留）
- **API 呼叫**：`apiGet<WatchlistOverviewRow[]>("/api/watchlist/overview")`、**🆕** `apiGetOptional<TagCount[]>("/api/watchlist/tags")`
- **主要 sections**：
  - PageHeader
  - Type tabs：個股 / ETF
  - **🆕** Tag filter chip 列：`<FilterChip>`「全部」+ 每個 tag 一個 chip（含命中數 badge）；無 tag 時整段隱藏
  - 綜合評分前三 / 後三 RankingCard
  - 全部自選股表，每列代號旁 **🆕** 渲染 tag chips（brand-tint 底色），含「今日%」`<PriceCell>`
- **用到 components**：`Icon`、`PageHeader`、`EmptyState`、`ScoreBadge`、`RecommendationTag`、`PriceCell`、`FilterChip`、`Th/Td`

### `/watchlist-manage` — 自選股管理

- **檔案**：[web/app/watchlist-manage/page.tsx](../web/app/watchlist-manage/page.tsx) + [client.tsx](../web/app/watchlist-manage/client.tsx)
- **類型**：RSC（`revalidate: 0`，每次重抓） + Client
- **API 呼叫**：
  - RSC：`/api/watchlist`（含 tags）
  - Client：`/api/watchlist/lookup/{id}`、POST `/api/watchlist`、POST `/api/watchlist/bulk-add`、POST `/api/watchlist/bulk-remove`、**🆕** PUT `/api/watchlist/{id}/tags`
- **主要 sections**：新增 / 批次新增、批次移除、列表（含 **🆕** 「標籤」欄位 `<TagsEditor>`：chip + × 移除、Enter / blur 新增；樂觀更新 + 失敗 rollback）

### `/dq` — 資料品質

- **檔案**：[web/app/dq/page.tsx](../web/app/dq/page.tsx)（317 行）
- **類型**：RSC（`revalidate: 60`）
- **searchParams**：`days (3-60), kind, sev`
- **API 呼叫**：`/api/dq/summary?days={N}`、`/api/dashboard/data-freshness`
- **主要 sections**：
  - PageHeader（line 67）
  - 窗口切換 5/10/20/60 日（line 74）
  - KPI row × 5（line 96）：總異常/嚴重/警告/提醒/缺值
  - 各表新鮮度（line 106）
  - 篩選 chip（line 121）：嚴重度 + 類型
  - 異常列表（line 178）
  - 缺值列表（line 254）
  - 建議行動（line 299）
- **用到 components**：`Icon`、`PageHeader`、`EmptyState`、`KPIStat`、`DataFreshnessBadge`、`Th/Td (compact)`

### `/sectors` — 族群輪動

- **檔案**：[web/app/sectors/page.tsx](../web/app/sectors/page.tsx)
- **類型**：RSC（`revalidate: 60`）+ 一個 Client 子元件（`<IndustryHeatmap>`）
- **searchParams**：`industry`（drill-down 用，存在時整頁切成「該產業成員股」單一視圖）
- **API 呼叫**（總覽）：`apiGetOptional<MarketBreadth>("/api/market/breadth")`、`apiGet<IndustryRotationResponse>("/api/market/industry-rotation")`（回傳 `{ asOf, rows }`，rows 含等權報酬 + 成交值加權當日報酬 + 漲跌持平家數 + 成交值）
- **API 呼叫**（drill-down）：`apiGetOptional<IndustryMemberRow[]>("/api/market/industry-members?industry=...&top=30")`
- **主要 sections（總覽）**：
  - 市場 breadth strip × 4
  - 產業熱度排行表：1/5/20/60 日報酬 + 熱度（每列「看成員」可進 drill-down）
  - 產業熱力圖 `<IndustryHeatmap>`：Goodinfo 風格 treemap，磚面積=該產業最新交易日成交值占大盤比、磚色=成交值加權當日報酬（紅漲綠跌、固定 ±10% 共 11 階色階）；hover 卡片錨在磚右外側、點磚進 drill-down
- **主要 sections（drill-down）**：麵包屑（族群輪動 › {產業}）+ 成員股表
- **用到 components**：`Icon`、`PageHeader`、`EmptyState`、`PriceCell`、`Th/Td (compact)`、`IndustryHeatmap`

### `/dividend-calendar` — 除權息行事曆

- **檔案**：[web/app/dividend-calendar/page.tsx](../web/app/dividend-calendar/page.tsx)（192 行）
- **類型**：RSC（`revalidate: 1800`，對齊後端 cache TTL）
- **searchParams**：`days (7/30/60/90), tab (holdings/watchlist/all)`
- **API 呼叫**：`apiGet<ExDividendCalendarEvent[]>("/api/calendar/ex-dividend?days_ahead={N}")`
- **主要 sections**：
  - PageHeader（line 51）
  - 未來天數切換（line 58）
  - 持股 / 自選 / 全部 tabs（line 83）含計數
  - 行事曆表（line 108）：日期 / 代號 / 類型 / 前收盤 / 參考價 / 權息值 / 殖利率
- **用到 components**：`Icon`、`PageHeader`、`EmptyState`、`Th/Td`

### `/backtest` — 策略回測

- **檔案**：[web/app/backtest/page.tsx](../web/app/backtest/page.tsx)（450 行）
- **類型**：RSC form，POST 用 GET 參數帶入後 RSC 內 `apiPost`（`revalidate: 0`）
- **searchParams**：`stockId, entry, exit, sl, tp, maxHold, slippage, lookback`
- **API 呼叫**：`apiGet<WatchlistEntry[]>("/api/watchlist")`、POST `/api/backtest/stock`
- **主要 sections**：
  - PageHeader（line 86）
  - 情景預設 × 3（line 93）：用 [lib/scenarios.ts](../web/lib/scenarios.ts) `BACKTEST_SCENARIOS`
  - 參數表單（line 147）：股票代號 + 4 主滑桿 + `<details>` 進階 4 滑桿
  - 一句話結論卡（line 222 / `<ConclusionCard>`）
  - KPI row × 6（line 235）：交易次數/勝率/平均/累積/MDD/Alpha
  - `<BacktestEquityChart>` 含進出場 scatter（line 274）
  - 交易明細表（line 291）含出場原因 chip
  - 套用設定（pre 包 details，line 347）
  - `<NextStepCards>` × 3（line 357）
- **用到 components**：`Icon`、`PageHeader`、`EmptyState`、`KPIStat`、`NextStepCards`、`Field`、`Th/Td (compact)`、`BacktestEquityChart`

### `/portfolio-backtest` — 投組回測

- **檔案**：[web/app/portfolio-backtest/page.tsx](../web/app/portfolio-backtest/page.tsx)（343 行）
- **類型**：RSC form（`revalidate: 0`）
- **searchParams**：`run, source (watchlist/custom), tickers, entry, exit, sl, tp, maxHold, slippage, lookback`
- **API 呼叫**：`/api/watchlist`、POST `/api/backtest/portfolio`
- **主要 sections**：
  - 情景預設 × 3（line 95）
  - 表單（line 145）：股票來源 radio + 自訂 textarea + 滑桿 + 進階
  - 結論卡（line 213 / `<PortfolioConclusion>`）
  - KPI row × 6（line 216）：檔數/加權勝率/平均策略/平均 B&H/平均 Alpha/0050 B&H
  - 每檔明細表（line 225）依 Alpha 降序，含 vs 0050 / vs 加權
  - `<NextStepCards>`（line 273）
- **用到 components**：`Icon`、`PageHeader`、`EmptyState`、`KPIStat`、`InfoTip`、`NextStepCards`、`Field`、`Th/Td (compact)`

### `/event-backtest` — 除權息事件回測

- **檔案**：[web/app/event-backtest/page.tsx](../web/app/event-backtest/page.tsx)（327 行）
- **類型**：RSC form（`revalidate: 0`）
- **searchParams**：`run, source, tickers, entry (-20~5), exit (1~60), year (2015~2024), minDiv`
- **API 呼叫**：`/api/watchlist`、POST `/api/backtest/event-driven`
- **主要 sections**：
  - 情景預設 × 3（line 83，用 `EVENT_SCENARIOS`）
  - 表單（line 124）：時點 D-N / D+M、起始年、最低股利
  - `<Conclusion>`（line 247）
  - KPI row × 6（line 179）：事件數/勝率/平均含息/純價差/平均殖利率/最佳最差
  - 每檔表現表（line 188）
  - 逐筆事件 `<TradesTable>`（line 229）
- **用到 components**：`Icon`、`PageHeader`、`EmptyState`、`KPIStat`、`Field`、`Th/Td (compact)`

### `/grid-search` — 參數掃描

- **檔案**：[web/app/grid-search/page.tsx](../web/app/grid-search/page.tsx)（444 行）
- **類型**：RSC form（`revalidate: 0`）
- **searchParams**：`run, mode (grid/wf), source, tickers, entries, exits, sls, tps, lookback, maxHold, splits, train`
- **API 呼叫**：`/api/watchlist`、POST `/api/backtest/grid-search` 或 POST `/api/backtest/walk-forward`
- **主要 sections**：
  - PageHeader（line 129）
  - 掃描範圍預設（保守/平衡/激進，line 136）
  - 模式切換（grid / walk-forward，line 185）含 InfoTip
  - 表單：4 個 list 輸入（entries/exits/sls/tps）+ lookback / maxHold
  - 結果（grid）：最佳 KPI、ranking 表
  - 結果（walk-forward）：分裂表 + 過擬合警告
- **用到 components**：`Icon`、`PageHeader`、`EmptyState`、`KPIStat`、`InfoTip`、`NextStepCards`、`Field`、`Th/Td (compact)`

### `/weight-tuner` — 權重調優

- **檔案**：[web/app/weight-tuner/page.tsx](../web/app/weight-tuner/page.tsx)（52 行 RSC shell）+ [client.tsx](../web/app/weight-tuner/client.tsx)（主體互動）
- **類型**：RSC（`revalidate: 0`） + Client
- **API 呼叫**：
  - RSC：`apiGet<TunerBreakdownResponse>("/api/weight-tuner/breakdown")`、`apiGet<PresetListResponse>("/api/weight-tuner/presets")`、`apiGetOptional<VisibleKeysResponse>("/api/weight-tuner/presets/visible-keys")`
  - Client：`apiGet/Post/Delete /api/weight-tuner/presets/...`
- **主要功能（client 內）**：19 個子指標 slider + 主題式 preset + 重新算分 / 儲存 / 刪除 user preset
- **特色**：所有計算在前端（純 JS 加權平均），不會弄壞後端資料
- **用到 components**：`Icon`、`InfoTip`、`ScoreBadge`、`NextStepCards`

---

## 共用 Components

### primitives/

| 檔案 | RSC/Client | 用途 | 主要 props |
|---|---|---|---|
| [Icon.tsx](../web/components/primitives/Icon.tsx) | RSC | Material Symbols 包裝 | `name, size, weight, filled, grade, label` |
| [PageHeader.tsx](../web/components/primitives/PageHeader.tsx) | RSC | 頁標題 + icon + 描述 | `title, icon, description, extra` |
| [Table.tsx](../web/components/primitives/Table.tsx) | RSC | 共用 Th/Td | `align (l/c/r), numeric, size (comfortable/compact), className` |
| [Pagination.tsx](../web/components/primitives/Pagination.tsx) | RSC | 分頁器（固定 7 格） | `page, totalPages, buildHref` |
| [KPIStat.tsx](../web/components/primitives/KPIStat.tsx) | RSC | KPI 卡 | `label, value, delta, deltaPct, deltaText, tone, footnote, size, term` |
| [ScoreBadge.tsx](../web/components/primitives/ScoreBadge.tsx) | RSC | 分數徽章（5 階色） | `score, size (sm/md/lg), horizon (short/mid/long/composite), ariaLabel` |
| [ScoreBreakdownBars.tsx](../web/components/primitives/ScoreBreakdownBars.tsx) | RSC | 子項分數橫條 | `parts: Record<string, number\|null>` |
| [RecommendationTag.tsx](../web/components/primitives/RecommendationTag.tsx) | RSC | 建議 tag（強買到強賣） | `raw, size (sm/md)` |
| [PriceCell.tsx](../web/components/primitives/PriceCell.tsx) | RSC | 價格 + 漲跌 | `price, prevClose, deltaPct, variant (compact/default/expanded), align` |
| [HoldingsTable.tsx](../web/components/primitives/HoldingsTable.tsx) | RSC | 持股表（首頁/持股共用） | `rows: HoldingRow[]` |
| [RadarHitChip.tsx](../web/components/primitives/RadarHitChip.tsx) | RSC | 首頁雷達命中 chip | `hit: RadarHit` |
| [RiskAlertList.tsx](../web/components/primitives/RiskAlertList.tsx) | RSC | 風險提醒（同 severity 合併成 list 卡） | `alerts: RiskAlert[]` |
| [SnapshotFreshnessIndicator.tsx](../web/components/primitives/SnapshotFreshnessIndicator.tsx) | Client | Topbar 快照新鮮度指示 + 一鍵重算 | `initial: SnapshotStatus \| null` |
| [SnapshotDeltaPanel.tsx](../web/components/primitives/SnapshotDeltaPanel.tsx) | RSC | 戰情室「今日 vs 昨日」delta | `delta: SnapshotDelta` |
| [TableScrollHint.tsx](../web/components/primitives/TableScrollHint.tsx) | Client | mobile 表格水平捲漸層 + 箭頭提示 | `children` |
| [SectionTitle.tsx](../web/components/primitives/SectionTitle.tsx) | RSC | 區塊標題（icon + 文字） | `children, icon` |
| [DataFreshnessBadge.tsx](../web/components/primitives/DataFreshnessBadge.tsx) | RSC | 資料新鮮度 badge | `tone (ok/warning/error/neutral), latestDate, lagDays` |
| [EmptyState.tsx](../web/components/primitives/EmptyState.tsx) | RSC | 空狀態包框 | `children, size (sm/md), tone, className` |
| [Field.tsx](../web/components/primitives/Field.tsx) | RSC | 表單欄位 wrapper（含 hint + InfoTip） | `label, hint, term, children, className` |
| [NextStepCard.tsx](../web/components/primitives/NextStepCard.tsx) | RSC | 「下一步試試」卡片群 | `items: NextStep[], heading` |
| [BackendDownError.tsx](../web/components/primitives/BackendDownError.tsx) | RSC | 後端掛掉時的友善錯誤頁 | `error, pageTitle` |
| [DownloadCsvButton.tsx](../web/components/primitives/DownloadCsvButton.tsx) | Client | 純前端 CSV 下載（Blob + BOM） | `headers, rows, filename, label, size, disabled` |
| [DownloadXlsxButton.tsx](../web/components/primitives/DownloadXlsxButton.tsx) | RSC | 🆕 Excel 下載（後端 API → `<a download>`，無 hooks） | `href, label, size, disabled` |
| [FilterChip.tsx](../web/components/primitives/FilterChip.tsx) | RSC | 統一 filter chip（active/inactive；focus-visible ring + aria-current） | `href, onClick, active, size, tone, icon, count, prefetch, ariaLabel` |
| [InfoTip.tsx](../web/components/primitives/InfoTip.tsx) | Client | 名詞解釋 tooltip（hover/click） | `term?, text?, inline, className` |
| [ThemeToggle.tsx](../web/components/primitives/ThemeToggle.tsx) | Client | 淺/系統/深 主題切換 | — |
| [SearchTrigger.tsx](../web/components/primitives/SearchTrigger.tsx) | Client | Topbar 搜尋按鈕（派發 event） | — |
| [CommandPalette.tsx](../web/components/primitives/CommandPalette.tsx) | Client | 全域 ⌘K 搜尋 / 快捷導航 | — |

### charts/

| 檔案 | RSC/Client | 用途 | 函式庫 |
|---|---|---|---|
| [CandlestickChart.tsx](../web/components/charts/CandlestickChart.tsx) | Client | K 線 + MA20/60 + 成交量 | `lightweight-charts` |
| [ScoreTimelineChart.tsx](../web/components/charts/ScoreTimelineChart.tsx) | Client | 分數折線（短/中/長/綜合） | Recharts |
| [BacktestEquityChart.tsx](../web/components/charts/BacktestEquityChart.tsx) | Client | 收盤 + 進場/出場 scatter | Recharts |
| [IndustryHeatmap.tsx](../web/components/charts/IndustryHeatmap.tsx) | Client | 產業熱力圖 treemap（成交值占比=面積、加權當日報酬=顏色、固定 ±10% 11 階色階、動態字級、字超磚截斷、hover 卡片錨磚右側）| `d3-hierarchy`（`treemapSquarify`，自繪 SVG）|

> **視覺驗證**：改 chart / 視覺類元件後，跑 `cd web && node scripts/screenshot.mjs`（需 `npm i -D playwright` + `npx playwright install chromium`，已列入 devDeps）。腳本會在 `web/scripts/screenshots/` 產出 light/dark × normal/hover 共 4 張，並把磚塊最差 5 個 aspect ratio 印到 stdout 供 sanity check。dev server (`:3000`) + FastAPI (`:8000`) 必須先起來。

### layout/

| 檔案 | RSC/Client | 用途 |
|---|---|---|
| [Sidebar.tsx](../web/components/layout/Sidebar.tsx) | Client | 左側固定導航（15 個 NAV 項，依 `usePathname` 高亮當前頁） |
| [Topbar.tsx](../web/components/layout/Topbar.tsx) | RSC（async） | 加權指數 + 漲跌家數 + breadth health label + 日期 + 搜尋 + 主題切換 |

---

## API Client（[lib/api.ts](../web/lib/api.ts)）

### Helper functions

- `apiGet<T>(path, opts?)`：一般 GET，預設 ISR `revalidate: 60`，`opts.noCache=true` 改為 `cache: "no-store"`，`opts.revalidate` 自訂秒數
- `apiGetOptional<T>(path, opts?)`：包 try/catch；失敗（404/422 等）回 `null` 不 throw
- `apiPost<T>(path, body)`：JSON POST，`cache: "no-store"`；錯誤從 `detail` 取
- `apiDelete<T>(path)`：DELETE，204 自動回 `undefined`
- `humanizeApiError(raw)`：把後端 detail / HTTP 錯誤翻成中文（insufficient_data、no_trades、422、500、Network…）

### TS types（與後端 Pydantic 1:1）

| TS type | 後端 schema | 用途 |
|---|---|---|
| `MarketSnapshot` | MarketSnapshot | Topbar 加權指數 |
| `MarketBreadth` | MarketBreadth | Topbar / sectors 市場廣度 |
| `PortfolioSummary` | PortfolioSummary | dashboard / holdings KPI |
| `HoldingRow` | HoldingRow | 持股列 |
| `RiskAlert` | RiskAlert | 風險提醒 |
| `RadarHit` | RadarHit | 雷達命中列 |
| `RadarStrategy` | RadarStrategy | 雷達策略 chip |
| `TradeRow` | TradeRow | 交易記錄 |
| `RealizedPnlRow` / `RealizedPnlSummary` | RealizedPnl* | 已實現損益 |
| `WatchlistMover` | WatchlistMover | 自選漲跌幅榜 |
| `WatchlistOverviewRow` | WatchlistOverviewRow | 自選總覽列 |
| `IndustryRotationRow` | IndustryRotationRow | 產業熱度 |
| `IndustryMemberRow` | IndustryMemberRow | 產業成員股 |
| `ExDividendEvent` | ExDividendEvent | dashboard 近 7 日除權息 |
| `ExDividendCalendarEvent` | ExDividendCalendarEvent | 行事曆完整事件 |
| `HistoryPerfRow` / `HistoryPerfSummary` | HistoryPerf* | 歷史命中表現 |
| `BacktestConfig` / `BacktestTrade` / `BacktestDailyPoint` / `BacktestSummary` / `BacktestResponse` | Backtest* | 個股回測 |
| `PortfolioRow` / `PortfolioAggregate` / `PortfolioBacktestResponse` | Portfolio* | 投組回測 |
| `GridSearchRow` / `GridSearchResponse` | GridSearch* | 參數網格 |
| `WalkForwardSplitRow` / `WalkForwardResponse` | WalkForward* | 步進前向 |
| `StockBreakdown` / `DefaultWeights` / `TunerBreakdownResponse` | TunerBreakdown* | 權重調優拆解 |
| `WeightSet` / `BuiltinPreset` / `UserPreset` / `PresetListResponse` | Preset* | 權重 preset |
| `VisibleKeysResponse` | VisibleKeysResponse | preset 可見子項 |
| `DataFreshness` | DataFreshness | 表新鮮度 |
| `StockMeta` | StockMeta | 個股 meta |
| `OHLCV` / `IndicatorPoint` / `StockPriceBundle` | StockPriceBundle | 個股價格捆包 |
| `ScoreParts` / `StockScoreView` | StockScoreView | 個股評分視圖 |
| `ScoreHistoryPoint` | ScoreHistoryPoint | 分數走勢點 |
| `PriceAnomaly` / `StockGap` / `DqSummary` | DQ* | 資料品質 |
| `SearchHit` | SearchHit | CommandPalette 搜尋結果 |
| `EventTradeRow` / `StockEventStatsRow` / `EventBacktestSummary` / `EventBacktestRequest` / `EventBacktestResponse` | EventBacktest* | 除權息事件回測 |

---

## 設計 tokens / 視覺一致性

- **漲紅跌綠**：`--color-up` / `--color-down`，文字 fg / 背景 bg / 邊框 border 三組
- **分數色階**（5 階）：`--score-strong-pos-*`（≥70）、`--score-pos-*`（≥55）、`--score-neutral-*`（≥45）、`--score-caution-*`（≥30）、`--score-danger-*`（<30）
- **建議色**：`--reco-buy-*` / `--reco-hold-*` / `--reco-sell-*`
- **狀態色**：`--info-*`、`--warning-*`、`--error-*`
- **品牌色**：`--brand-{50,200,300,500,600,700}`
- **chart 專用**：`--chart-grid`、`--chart-axis`、`--chart-tooltip-{bg,fg}`
- **K 線**：陽線（紅）`--up-500`、陰線（綠）`--down-500`
- **fonts**：Noto Sans TC（CJK） + JetBrains Mono（數字 `numeric` class 用 tabular-nums）

### ScoreBadge 跨頁面統一規則

- **列表表格中所有 horizons 都用 `size="sm"`**（避免「短/中/長 sm 但綜合 md」的不一致）
- 個股詳情頁的「分數拆解」block 也用 `size="sm"`
- watchlist Top/Bottom RankingCard 中的綜合分數獨立可用 `size="md"`（已確認）

### 表格慣例

- listing 頁（雷達/自選/持股/行事曆/歷史）：Td `size="comfortable"`（h-14）
- 工具/結果頁（DQ/sectors/backtest 結果/grid/event/dividend-cal）：Td `size="compact"`（h-12）
- 多分頁列表用 `table-fixed` + 顯式 Th 寬度，避免分頁切換時欄寬抖動
- 數字欄統一 `numeric` class（tabular-nums）+ `align="right"`

---

## 異常 / 觀察（順便產生的副產品）

> **2026-04-26 更新**：1、2、3、5、6 已修復；4 留作 future polish。

1. ~~`HoldingsTable.tsx` 內部自定 `Th/Td`~~ ✅ 已改用共用 `Table.tsx`，並加 `table-fixed` + 顯式欄寬。
2. ~~CommandPalette 的 ACTIONS 缺項~~ ✅ 已補 `/dq`、`/event-backtest`。
3. ~~Sidebar 「個股詳情」固定指向 `/stocks/2330`~~ ✅ 已移除該條目（⌘K 搜代號或從表格列點進去）。
4. **`/event-backtest` 不使用 `<NextStepCards>`** — 其他三個進階頁有，這頁沒接。留作 future polish。
5. ~~`grid-search/page.tsx` 沒 try/catch~~ ✅ 已加 `<BackendDownError>` 保護。
6. ~~`event-backtest/page.tsx` import 但未使用 `BackendDownError`~~ ✅ 已補上實際 try/catch。
7. **所有 component 都至少被一個頁面 import**（無 dead code）。

## 2026-04-26 新增

### 共用 component

- **DownloadCsvButton** [../web/components/primitives/DownloadCsvButton.tsx](../web/components/primitives/DownloadCsvButton.tsx) — client-side CSV 產出（Blob → download），帶 BOM 對齊 Excel 繁中。
  ```tsx
  <DownloadCsvButton<RowType>
    rows={rows}
    columns={[{label, value: r => ...}]}
    filename="prefix"
    size="sm"
  />
  ```
  接上的頁面：`/radar`、`/dividend-calendar`、`/portfolio-backtest`。

- **TradesPanel** [../web/app/holdings/TradesPanel.tsx](../web/app/holdings/TradesPanel.tsx) — 持股頁的「交易紀錄」整段，含新增表單與刪除按鈕。client component。

### 既有 component / API client 改動

- `lib/api.ts` `apiDelete` 加 204 處理（避免空 body 撞 `res.json()`）
- `lib/api.ts` `humanizeApiError` 新增 `ECONNREFUSED|fetch failed|ECONNRESET` 對應，後端掉時錯誤訊息友善化
- 列表表格的「綜合」`ScoreBadge` 統一 `size="sm"`（雷達/歷史/自選；之前是 md，與短中長 sm 不一致）
- `/backtest`、`/portfolio-backtest` 補 try/catch + `<BackendDownError>` 保護（之前後端掉就直接炸頁，與其他 12 頁不一致）

### Design tokens 變更

[web/styles/tokens.css](../web/styles/tokens.css) 新增以下 token：

**Tooltip（InfoTip 與 Chart Tooltip 同源）**
```css
/* light */
--tooltip-bg: var(--neutral-900);
--tooltip-fg: #FFFFFF;
--tooltip-border: rgba(255, 255, 255, 0.08);
--chart-tooltip-bg: var(--tooltip-bg);
--chart-tooltip-fg: var(--tooltip-fg);

/* dark：反向（淺底深字） */
--tooltip-bg: #F8FAFC;
--tooltip-fg: #0B0D11;
--tooltip-border: rgba(0, 0, 0, 0.08);
```

`InfoTip` 從錯誤的 `text-[var(--surface)]`（未定義 → 看不到字）改用 `text-[var(--tooltip-fg)]`。

**Chart series 色（短/中/長/綜合分數線、K 線 MA20/MA60）**
```css
/* light */
--chart-series-short:     var(--up-500);    /* #D9342B */
--chart-series-mid:       var(--brand-500); /* #2F4A80 */
--chart-series-long:      var(--down-500);  /* #16A34A */
--chart-series-composite: #7048E8;          /* violet */
--chart-ma20:             var(--brand-500);
--chart-ma60:             #7048E8;

/* dark：較亮版本，深底上對比夠 */
--chart-series-short:     #EC6558;
--chart-series-mid:       #7E93BE;  /* brand-300 */
--chart-series-long:      #4FB872;
--chart-series-composite: #9D8DF1;  /* violet 亮版 */
--chart-ma20:             #7E93BE;
--chart-ma60:             #9D8DF1;
```

> **composite 與 ma60 都用紫色** — 因為 composite 在分數折線圖、ma60 在 K 線圖，永遠不同框出現，可共用同一抹紫。

### Chart 元件色彩接法

兩種接法視 chart lib 而定：

- **Recharts**（SVG，支援 CSS var()）— 直接 `stroke="var(--chart-series-short)"`，dark/light 切換自動跟隨。範例：[ScoreTimelineChart.tsx](../web/components/charts/ScoreTimelineChart.tsx)、[BacktestEquityChart.tsx](../web/components/charts/BacktestEquityChart.tsx)
- **lightweight-charts**（canvas，不支援 CSS var()）— 用 `getComputedStyle(document.documentElement).getPropertyValue('--chart-ma20')` 在 useEffect 抓現值。範例：[CandlestickChart.tsx](../web/components/charts/CandlestickChart.tsx)

LegendDot 等純 DOM 元素用 inline style：`style={{ backgroundColor: "var(--chart-series-short)" }}` 即可。

### 全專案 hard-coded 色值清查（2026-04-26）

掃過 `web/app/` 與 `web/components/`：
- `BacktestEquityChart.tsx` 兩處 `stroke="#fff"` — **保留**（圓點外框，dark 上仍可見）
- 其他全部走 token

未來新增 chart series 請優先擴 `--chart-series-*` 而非 hard-code 顏色。

### 對應後端新增的工具 endpoints

未來在前端做 admin / settings 頁時可呼叫：
- `/api/system/snapshot-status` — 顯示「分數新鮮度」徽章
- `/api/system/notify-test` — 設定頁試 Discord webhook
- `/api/system/run-log` — 排程歷史
- `/api/system/backup-now` — 手動備份
- `/api/system/refresh-snapshot` — 強制重跑 snapshot
- `/api/stocks/{id}/atr-stop` — 個股詳情頁可以加「ATR 建議停損」卡片
- `/api/portfolio/position-suggest` — 個股詳情頁可以加「該買幾張」試算

完整 API 規格 → [api-spec.md](api-spec.md)
