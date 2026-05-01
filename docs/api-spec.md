# 後端 API 規格

> 自動產出於 2026-04-26。修改 API 時請同步更新本檔。
> Camel/Snake 約定：FastAPI 的 alias_generator 是 `to_camel`（見 [api/schemas/common.py](../api/schemas/common.py)），request body 接 snake_case，response 出 camelCase（前端 TypeScript 直接拿即可）。

## 全域慣例
- **BASE URL**：`http://localhost:8000`
- **CORS**：開發期允許 `http://localhost:3000` / `http://127.0.0.1:3000`（[api/main.py:25](../api/main.py#L25)）
- **錯誤格式**：`HTTPException` → `{"detail": "..."}`；常見 status code：`400`（請求錯誤）、`404`（資源不存在）、`409`（衝突，自選股已存在）、`413`（請求過大，回測超量）、`422`（資料不足以計算）、`500`（後端未預期錯誤）
- **Pydantic 序列化**：所有 response model 繼承 `CamelModel`（[api/schemas/common.py:8](../api/schemas/common.py#L8)）。前端拿到 `stockId` / `stockName` / `compositeScore`；後端內部用 `stock_id` / `stock_name` / `composite_score`。
- **NaN/inf 處理**：Pydantic v2 拒絕 NaN/inf，router 層用 `safe_float()` ([api/common.py:15](../api/common.py#L15)) 轉成 `None`
- **snapshot 新鮮度**：列表型 router（radar / watchlist / portfolio / dashboard）開頭呼叫 `ensure_fresh(db)`（[app/scoring/snapshot_freshness.py](../app/scoring/snapshot_freshness.py)），signal_history 比 daily_price 舊時自動補跑當日快照
- **list 預設排序**：雷達 / 自選總覽依 `composite` 降序；交易紀錄依 `trade_date DESC, id DESC`；漲跌排行依 `change_pct` 升/降序
- **市場分類**：`classify_market(stock_id, type)` ([app/data/market_type.py:22](../app/data/market_type.py#L22)) 回傳 `"上市" | "上櫃" | "ETF" | "其他"`

## 路由總覽

| Method | Path | Router | 用途 |
|---|---|---|---|
| GET | /api/health | [main.py:47](../api/main.py#L47) | 健康檢查 |
| GET | /api/market/snapshot | [market.py:37](../api/routers/market.py#L37) | 加權指數最新收盤 |
| GET | /api/market/breadth | [market.py:63](../api/routers/market.py#L63) | 市場寬度 + 健康度標籤 |
| GET | /api/market/industry-rotation | [market.py:86](../api/routers/market.py#L86) | 產業輪動排行（含 totalAmount） |
| GET | /api/market/industry-members | [market.py:106](../api/routers/market.py#L106) | 指定產業成分股 |
| GET | /api/portfolio/holdings | [portfolio.py:67](../api/routers/portfolio.py#L67) | 持股總覽 + 風險訊號 |
| GET | /api/portfolio/summary | [portfolio.py:117](../api/routers/portfolio.py#L117) | 持股聚合（市值 / 損益） |
| GET | /api/portfolio/risk-alerts | [portfolio.py:146](../api/routers/portfolio.py#L146) | 風險訊號彙整（含集中度） |
| GET | /api/portfolio/trades | [portfolio.py:168](../api/routers/portfolio.py#L168) | 交易紀錄列表 |
| POST | /api/portfolio/trades | [portfolio.py:203](../api/routers/portfolio.py#L203) | 新增交易（買 / 賣） |
| DELETE | /api/portfolio/trades/{trade_id} | [portfolio.py:242](../api/routers/portfolio.py#L242) | 刪除交易並重建持股 |
| GET | /api/portfolio/realized-pnl | [portfolio.py:248](../api/routers/portfolio.py#L248) | 已實現損益（FIFO 配對） |
| GET | /api/watchlist | [watchlist.py:30](../api/routers/watchlist.py#L30) | 自選股清單 |
| POST | /api/watchlist | [watchlist.py:47](../api/routers/watchlist.py#L47) | 新增自選 |
| DELETE | /api/watchlist/{stock_id} | [watchlist.py:75](../api/routers/watchlist.py#L75) | 移除自選 |
| POST | /api/watchlist/bulk-add | [watchlist.py:83](../api/routers/watchlist.py#L83) | 批次新增 |
| POST | /api/watchlist/bulk-remove | [watchlist.py:101](../api/routers/watchlist.py#L101) | 批次移除 |
| GET | /api/watchlist/lookup/{stock_id} | [watchlist.py:107](../api/routers/watchlist.py#L107) | 代號 → 名稱 |
| GET | /api/watchlist/movers | [watchlist.py:120](../api/routers/watchlist.py#L120) | 自選股漲跌排行 |
| GET | /api/watchlist/overview | [watchlist.py:186](../api/routers/watchlist.py#L186) | 自選股總覽（分數 + 漲跌） |
| GET | /api/stocks/{stock_id}/meta | [stocks.py:27](../api/routers/stocks.py#L27) | 個股 meta（名稱 / 產業） |
| GET | /api/stocks/{stock_id}/price | [stocks.py:51](../api/routers/stocks.py#L51) | 個股 K 線 + 技術指標 |
| GET | /api/stocks/{stock_id}/score | [stocks.py:85](../api/routers/stocks.py#L85) | 個股短/中/長期評分（支援 `?live=1` / `?override_price=X` 重算） |
| GET | /api/stocks/{stock_id}/intraday | [stocks.py](../api/routers/stocks.py) | **🆕** 盤中即時報價（mis.twse.com.tw，30s cache） |
| GET | /api/stocks/{stock_id}/score-history | [stocks.py:135](../api/routers/stocks.py#L135) | 個股分數歷史曲線 |
| GET | /api/dashboard/radar-hits | [dashboard.py:19](../api/routers/dashboard.py#L19) | 戰情室雷達命中（預設個股） |
| GET | /api/dashboard/ex-dividend | [dashboard.py:30](../api/routers/dashboard.py#L30) | 自選+持股近 N 日除權息 |
| GET | /api/dashboard/data-freshness | [dashboard.py:107](../api/routers/dashboard.py#L107) | 各資料表新鮮度燈號 |
| GET | /api/dashboard/snapshot-delta | [dashboard.py](../api/routers/dashboard.py) | **🆕** 戰情室「今日 vs 昨日」delta（新進命中 / 跌出命中 / 分數 \|Δ\|≥5） |
| GET | /api/radar/strategies | [radar.py:16](../api/routers/radar.py#L16) | 雷達策略 + 當日命中數 |
| GET | /api/radar/hits | [radar.py:39](../api/routers/radar.py#L39) | 雷達命中清單（依策略 / 市場過濾） |
| GET | /api/history/dates | [history.py:19](../api/routers/history.py#L19) | 可回看的歷史快照日 |
| GET | /api/history/strategies | [history.py:24](../api/routers/history.py#L24) | 指定 as_of 各策略命中數 |
| GET | /api/history/performance | [history.py:54](../api/routers/history.py#L54) | 歷史命中股票至今的表現 |
| GET | /api/calendar/ex-dividend | [calendar.py:74](../api/routers/calendar.py#L74) | 除權息行事曆（現場抓 TWSE，30 分快取） |
| POST | /api/backtest/stock | [backtest.py:57](../api/routers/backtest.py#L57) | 單股策略回測 |
| POST | /api/backtest/portfolio | [backtest.py:174](../api/routers/backtest.py#L174) | 多股策略回測（最多 50 檔） |
| POST | /api/backtest/grid-search | [backtest.py:250](../api/routers/backtest.py#L250) | 同步參數網格掃描（最多 80 組） |
| POST | /api/backtest/walk-forward | [backtest.py:305](../api/routers/backtest.py#L305) | Walk-forward 過擬合檢測 |
| POST | /api/backtest/event-driven | [backtest.py:363](../api/routers/backtest.py#L363) | 除權息事件回測 |
| GET | /api/weight-tuner/breakdown | [weight_tuner.py:52](../api/routers/weight_tuner.py#L52) | 自選股每檔子項分數 + 預設權重 |
| GET | /api/weight-tuner/presets | [weight_tuner.py:124](../api/routers/weight_tuner.py#L124) | 內建 + 使用者 preset 列表 |
| POST | /api/weight-tuner/presets | [weight_tuner.py:132](../api/routers/weight_tuner.py#L132) | 新增/更新使用者 preset |
| GET | /api/weight-tuner/presets/visible-keys | [weight_tuner.py:143](../api/routers/weight_tuner.py#L143) | 新手模式顯示子指標白名單 |
| DELETE | /api/weight-tuner/presets/{name} | [weight_tuner.py:148](../api/routers/weight_tuner.py#L148) | 刪除使用者 preset |
| GET | /api/search/stocks | [search.py:26](../api/routers/search.py#L26) | 跨頁面快搜（Cmd+K） |
| GET | /api/dq/summary | [dq.py:88](../api/routers/dq.py#L88) | 資料品質檢查（價格異常 + 缺值） |

## 各 Router 詳述

### system — 健康檢查

#### GET /api/health
- **用途**：liveness 探測
- **Response**：`{"status": "ok"}`

---

### market.py — 大盤 / 寬度 / 產業

#### GET /api/market/snapshot
- **用途**：加權指數（發行量加權股價指數）最新收盤
- **Response model**：`MarketSnapshot` { date, close, changePct }
- **備註**：無資料時所有欄位為 None

#### GET /api/market/breadth
- **用途**：市場寬度 — 上漲家數、新高新低、MA 站上比率、健康燈號
- **Response model**：`MarketBreadth`（含 `health_label` 中文與 `health_tone` 顏色）
- **依賴**：`market_breadth()` + `breadth_health_label()`（[app/indicators/market_scope.py:212](../app/indicators/market_scope.py#L212)）

#### GET /api/market/industry-rotation
- **用途**：產業輪動表（1d / 5d / 20d / 60d 等權報酬 + 1d 成交值加權報酬 + heat 指數 + total_amount 成交值 + 漲跌持平家數）
- **Query params**：`min_members` (int, 預設 3) — 至少有幾檔成員才納入
- **Response model**：`IndustryRotationResponse` `{ asOf, rows }`

#### GET /api/market/industry-members
- **用途**：指定產業內成員，預設依某報酬指標 top N
- **Query params**：`industry` (必填)、`top` (int, 預設 30)
- **錯誤**：400 = industry 為空
- **Response model**：`list[IndustryMemberRow]`

---

### portfolio.py — 持股總覽 + 損益 + 風險

#### GET /api/portfolio/holdings
- **用途**：持股每檔的市值、毛/淨損益、最新分數、風險訊號
- **Response model**：`list[HoldingRow]`
- **副作用**：開頭呼叫 `ensure_fresh()`

#### GET /api/portfolio/summary
- **用途**：持股聚合（總市值、總成本、毛/淨未實現損益、當日損益、預估賣出成本）
- **Response model**：`PortfolioSummary`

#### GET /api/portfolio/risk-alerts
- **用途**：風險警告 — 含個股 warning 與整體集中度提醒
- **Response model**：`list[RiskAlert]`（severity = `"info" | "warning" | "critical"`）

#### GET /api/portfolio/trades
- **用途**：交易紀錄（trade_log），最新優先
- **Query params**：`limit` (int, 預設 100)
- **Response model**：`list[TradeRow]`

#### POST /api/portfolio/trades
- **用途**：新增買/賣交易；更新 holdings
- **Request body**：`TradeCreateBody` { tradeDate (YYYY-MM-DD), stockId, action ("BUY"|"SELL"), shares (>0), price (>0), fee?, tax?, note? }
- **fee/tax 留空**：後端用 `effective_fee()` ([app/portfolio.py:32](../app/portfolio.py#L32)) 算手續費（0.1425% × broker 折扣，最低 broker.fee_min），賣方稅 0.3%
- **Status**：201
- **錯誤**：400 = action 非法 / shares <= 0
- **Response model**：`TradeRow`（含 stockName）

#### DELETE /api/portfolio/trades/{trade_id}
- **用途**：刪除交易並從 trade_log 重建該股 holdings
- **Status**：204（idempotent — 對不存在的 id 也 204）

#### GET /api/portfolio/realized-pnl
- **用途**：FIFO 配對的已實現損益（每筆配對 buy/sell + 該股總損益）
- **Response model**：`RealizedPnlSummary` { totalPnl, pairCount, winCount, winRate, rows[] }

---

### watchlist.py — 自選股 CRUD + 排行

#### GET /api/watchlist
- **Response model**：`list[WatchlistEntry]` { stockId, stockName }

#### POST /api/watchlist
- **Request body**：`AddBody` { stockId }
- **驗證**：必須在 `stock_info` 或 `daily_price` 至少存在一筆，避免寫入垃圾代號
- **錯誤**：400 = 代號為空；404 = 找不到股票；409 = 已在自選
- **Response**：`{ "ok": true, "stockName": "..." }`

#### DELETE /api/watchlist/{stock_id}
- **錯誤**：404 = 不在自選清單
- **Response**：`{ "ok": true }`

#### POST /api/watchlist/bulk-add
- **Request body**：`BulkAddBody` { stockIds: list[str] }
- **Response**：`{ "added": N, "skipped": M }`

#### POST /api/watchlist/bulk-remove
- **Request body**：`BulkRemoveBody` { stockIds: list[str] }
- **Response**：`{ "removed": N }`

#### GET /api/watchlist/lookup/{stock_id}
- **用途**：代號 → 名稱（給新增表單 auto-fill）
- **Response model**：`WatchlistLookup` { stockId, stockName? }

#### GET /api/watchlist/movers
- **用途**：自選股當日漲/跌幅排行
- **Query params**：`top` (int, 預設 5)、`direction` ("up"|"down", 預設 "up")
- **Response model**：`list[WatchlistMover]`

#### GET /api/watchlist/overview
- **用途**：自選股總覽，每檔最新分數 + 當日漲跌，依 composite 降序
- **Response model**：`list[WatchlistOverviewRow]`

---

### stocks.py — 個股詳情

#### GET /api/stocks/{stock_id}/meta
- **用途**：股票名稱、產業類別、市場類別
- **Path params**：`stock_id` (str)
- **Response model**：`StockMeta` { stockId, stockName, industry?, marketType? }
- **錯誤**：404 = 完全找不到（既無 stock_info、也無 daily_price）；有價格但無 stock_info 時仍回 200，stockName 用代號代

#### GET /api/stocks/{stock_id}/price
- **用途**：K 線 + 技術指標（MA/KD/RSI/Bollinger）
- **Query params**：`days` (int, 預設 180)
- **Response model**：`StockPriceBundle` { stockId, ohlcv[], indicators[] }
- **錯誤**：404 = 無 daily_price 資料

#### GET /api/stocks/{stock_id}/score
- **用途**：完整三維評分結果（含子項、推薦、進出場提示、警告）
- **Query params**（皆 optional）：
  - `live` (int, 0/1)：1 = 抓 mis 盤中即時價當作最新一筆 close 重算技術面分數；mis 失敗會自動 fallback 到收盤分數（不報錯）
  - `override_price` (float)：what-if 假設成交價，會覆寫最新一筆 close 重算；同時帶 `live=1` 時以 `override_price` 為準
  - 兩者都帶 → response 多出 `livePriceUsed=true` 與 `livePrice=<X>`，前端用來標示「盤中估算」徽章
  - **不影響長期分數**（吃 ROE/EPS/股利等財報指標，盤中價無關）
- **Response model**：`StockScoreView`（新增 `livePriceUsed: bool`、`livePrice: float | null`）
- **錯誤**：404 = 完全無資料；422 = 資料不足 60 個交易日無法評分
- **依賴**：`score_stock()`（[app/scoring/engine.py](../app/scoring/engine.py)）；mis client [app/data/intraday.py](../app/data/intraday.py)
- **注意**：本 endpoint 走「即時 compute」**不寫入 signal_history**，避免污染回測來源

#### GET /api/stocks/{stock_id}/intraday
- **用途**：盤中即時報價（TWSE mis.twse.com.tw）；給前端「即時模式」按鈕用
- **Response model**：`IntradayQuoteView` { stockId, price, prevClose, open, high, low, volumeLots, quoteTime, isLive, changePct }
  - `isLive=false`：mis 回 `z='-'`（盤前/休市），price 用昨收 fallback
- **錯誤**：422 = 興櫃 / mis 無回應 / 該股不在 mis（前端應隱藏「即時」按鈕並 fallback 收盤分數）
- **快取**：30 秒記憶體快取，避免 hammer 上游
- **依賴**：[app/data/intraday.py](../app/data/intraday.py)

#### GET /api/stocks/{stock_id}/score-history
- **用途**：個股分數歷史曲線（讀 signal_history）
- **Query params**：`days` (int, 預設 90)
- **Response model**：`list[ScoreHistoryPoint]`

---

### dashboard.py — 今日戰情室聚合

#### GET /api/dashboard/radar-hits
- **用途**：戰情室預設只看個股（上市/上櫃），ETF 評分機制不同所以排除
- **Query params**：`limit` (int, 預設 10)、`market` (list, 預設 `["上市","上櫃"]`)
- **Response model**：`list[RadarHit]`
- **副作用**：開頭呼叫 `ensure_fresh()`

#### GET /api/dashboard/ex-dividend
- **用途**：自選 + 持股近 N 日除權息
- **Query params**：`days_ahead` (int, 預設 7)
- **Response model**：`list[ExDividendEvent]`
- **資料源**：`adj_event` 表（除權息實際發生時的還原因子記錄）；`cash_dividend` = `before_price - after_price` 推估

#### GET /api/dashboard/data-freshness
- **用途**：各核心資料表的最新日期、延遲天數、燈號（ok / warning / error）
- **Response model**：`list[DataFreshness]`
- **涵蓋表**：daily_price / institutional / margin / per_pbr / monthly_revenue / financials_quarterly_derived / signal_history
- **燈號規則**：依表別 + 今天星期幾 動態計算 `_expected_lag`（週末扣天 / 月營收容忍 70 天 / 季財報容忍 180 天）

---

### radar.py — 雷達掃描

#### GET /api/radar/strategies
- **用途**：列出所有策略 + 當日命中數（以 signal_history.strategies 子字串比對）
- **Response model**：`list[RadarStrategy]`
- **策略名單範例**：`短線強勢 / 中期波段 / 長期價值 / 外資連買 / 回檔布局 / 三榜俱佳 / 相對強勢 / 月營收爆發 / 營收持續成長 / 營收高速加速 / 量能動能`
- **副作用**：開頭呼叫 `ensure_fresh()`

#### GET /api/radar/hits
- **用途**：當日 signal_history 依策略 + 市場過濾，依對應分數降序（短/中/長期/`量能動能` 各依自己的維度，其他 fallback composite）
- **Query params**：`strategy` (str, 可選)、`market` (list, 預設 `["上市","上櫃","ETF"]`)、`top` (int, 預設 50；`top=0` 視為「全部」不截斷)
- **Response model**：`list[RadarHit]`
- **副作用**：開頭呼叫 `ensure_fresh()`

---

### history.py — 歷史追蹤

#### GET /api/history/dates
- **用途**：列出有快照的歷史日期
- **Response**：`list[str]`（ISO 日期）

#### GET /api/history/strategies
- **用途**：指定 as_of 各策略命中數
- **Query params**：`as_of` (必填)、`market` (list, 預設 `["上市","上櫃","ETF"]`)
- **Response model**：`list[RadarStrategy]`

#### GET /api/history/performance
- **用途**：歷史快照命中股票，至今的表現追蹤
- **Query params**：`as_of` (必填)、`strategy` (可選)、`top` (int, 預設 0=全部)、`market` (list)
- **Response model**：`HistoryPerfSummary`（含 winRate, avgChangePct, rows[]）
- **錯誤**：404 = 當日無命中資料
- **備註**：統計（勝率/平均漲幅）由完整集合計算，不受截斷影響

---

### calendar.py — 除權息行事曆

#### GET /api/calendar/ex-dividend
- **用途**：未來 N 日除權息事件（現場從 TWSE TWT49U 抓）
- **Query params**：`days_ahead` (1..180, 預設 60)
- **Response model**：`list[ExDividendCalendarEvent]`（含 cumPrice / exPrice / yieldPct / inHoldings / inWatchlist 旗標）
- **快取**：30 分鐘 in-memory 純資料快取，watchlist/holdings flag 每次重算

---

### backtest.py — 策略回測

#### POST /api/backtest/stock
- **用途**：單股策略回測
- **Request body**：`BacktestRequest` { stockId, config? }
- **Response model**：`BacktestResponse`（含 summary、trades[]、dailySeries[]、resolved config）
- **錯誤**：400 = stockId 空；422 = 資料缺失/邊界錯誤；500 = 未預期錯誤（log full traceback）

#### POST /api/backtest/portfolio
- **用途**：多股策略回測 + 對標 0050 / TAIEX
- **Request body**：`PortfolioBacktestRequest` { stockIds, config? }
- **限制**：`stockIds` 最多 50 檔
- **錯誤**：400 = stockIds 空；413 = 超過 50 檔；422 = 回測失敗
- **Response model**：`PortfolioBacktestResponse`

#### POST /api/backtest/grid-search
- **用途**：同步參數網格掃描，挑 alpha 最高
- **Request body**：`GridSearchRequest` { stockIds, entryList, exitList, slList, tpList, maxHoldDays, slippageBps, lookbackDays }
- **限制**：`stockIds` 最多 20；組合 (entry × exit × sl × tp) 最多 80
- **錯誤**：400 = 空清單；413 = 超量
- **Response model**：`GridSearchResponse`（含 best, elapsedSec）

#### POST /api/backtest/walk-forward
- **用途**：時間切片過擬合檢測
- **Request body**：`WalkForwardRequest` { stockIds, entryList..tpList, nSplits, trainRatio }
- **Response model**：`WalkForwardResponse`（含 overfitWarning：train 比 test 高 5pp 或 test 平均 ≤0）

#### POST /api/backtest/event-driven
- **用途**：除權息事件驅動回測（前 N 日進、後 M 日出，含現金股利報酬）
- **Request body**：`EventBacktestRequest` { stockIds, entryOffset (-5), exitOffset (10), sinceYear (2020), minDividend (0.5) }
- **限制**：最多 100 檔
- **Response model**：`EventBacktestResponse`（含 summary、byStock[]、trades[]、configEcho）

---

### weight_tuner.py — 權重調優

#### GET /api/weight-tuner/breakdown
- **用途**：自選股每檔的子項分數 + 預設總分（前端 client-side 即時重算）
- **Response model**：`TunerBreakdownResponse` { stocks: StockBreakdown[], defaultWeights }

#### GET /api/weight-tuner/presets
- **用途**：內建主題 preset + 使用者自存 preset 清單
- **Response model**：`PresetListResponse` { builtin: BuiltinPreset[], user: UserPreset[] }

#### POST /api/weight-tuner/presets
- **用途**：新增/更新使用者 preset（同名 upsert）
- **Request body**：`PresetUpsertRequest` { name, description?, weights }
- **錯誤**：422 = weights 結構錯
- **Response model**：`UserPreset`

#### GET /api/weight-tuner/presets/visible-keys
- **用途**：新手模式每維度顯示的子指標白名單（[app/scoring/rubric.py BEGINNER_VISIBLE_KEYS]）
- **Response model**：`VisibleKeysResponse` { short: str[], mid: str[], long: str[] }

#### DELETE /api/weight-tuner/presets/{name}
- **錯誤**：404 = 找不到 preset；422 = 內建 preset 不可刪
- **Response**：`{ "deleted": name }`

---

### search.py — 跨頁面快搜

#### GET /api/search/stocks
- **用途**：模糊搜尋股票代號 / 名稱（Cmd+K palette）
- **Query params**：`q` (str, 預設 "")、`limit` (1..50, 預設 12)
- **Response model**：`list[SearchHit]` { stockId, stockName, market?, industry?, inWatchlist }
- **規則**：
  - 空字串 → 自選股優先 / 否則雷達 Top 命中
  - 純數字 → 代號嚴格匹配 → 前綴 → 子字串 排序
  - 含中文/英文 → 名稱前綴 → 子字串
  - 自選股置頂

---

### dq.py — 資料品質檢查

#### GET /api/dq/summary
- **用途**：價格異常 + 股票級缺值掃描
- **Query params**：`days` (3..60, 預設 10)
- **Response model**：`DqSummary`（含 anomalies[80]、gaps[30]、scope 描述）
- **掃描範圍**：自選 + 持股 + 雷達 Top 100
- **異常類型**：limit_up / limit_down (≥9.9%) / volume_spike (≥5× 20日均) / stale (連續 ≥3 日 close 不變) / huge_gap (>15% 且無除權息)
- **排序**：critical → warning → info；缺值依 missing_days 降序

---

## Pydantic Schemas

> 只列 router 用到的 response/request schemas；內部 helper（`Holding` dataclass、`StockScore` 等）跳過。

### Common — [api/schemas/common.py](../api/schemas/common.py)

#### CamelModel
- 所有 response DTO 基類；`alias_generator=to_camel`、`populate_by_name=True`、`from_attributes=True`

#### StockRef
- `stock_id: str`（必填）
- `stock_name: str`（必填）

#### StockRefOptional
- `stock_id: str`
- `stock_name: str | None = None`

#### ErrorResponse
- `code: str`
- `message: str`
- `detail: str | None`
- 註：實際錯誤格式是 FastAPI 預設 `{"detail": ...}`，這個 schema 暫未用於回應

---

### Stock — [api/schemas/stock.py](../api/schemas/stock.py)

#### StockMeta (StockRef)
- `industry: str | None`
- `market_type: str | None`

#### OHLCV
- `date, open, high, low, close: float`、`volume: float | None`

#### IndicatorPoint
- `date: str`，`ma5/ma20/ma60/k9/d9/rsi14/bb_upper/bb_lower: float | None`

#### ScoreParts
- `total: float | None`（資料不足為 None）
- `completeness: float`（有效子指標權重比，1.0 = 全齊）
- `parts: dict[str, float | None]`
  - **short keys**：`ma_alignment, kd, macd, rsi, bollinger, volume, vr_macd, foreign, trust, margin_change`
  - **mid keys**：`trend, foreign_cum, trust_cum, eps_growth, revenue_growth, vr_macd`
  - **long keys**：`roe, margin_quality, eps_cagr_3y, dividend, valuation`
  - `vr_macd`：純 VR26（成交量比率，台股 26 日慣用）分數，先依 VR 區間給 baseline，再依 VR 是否較前一日上升/下降做微調。short 權重 0.08、mid 權重 0.04。

#### StockScoreView (StockRef)
- `as_of: str`、`close: float`
- `short, mid, long: ScoreParts`
- `composite_score: float | None`、`data_completeness: float`
- `is_stale: bool`、`stale_days: int`
- `recommendation: str`
- `entry, stop_loss, take_profit, warnings: list[str]`

#### ScoreHistoryPoint
- `date: str`、`short/mid/long/composite: float | None`

#### StockPriceBundle
- `stock_id: str`、`ohlcv: list[OHLCV]`、`indicators: list[IndicatorPoint]`

#### RadarHit (StockRef)
- `close, short, mid, long, composite: float | None`
- `vr_macd: float | None`（VR26 純量能分；「量能動能」策略依此排序）
- `recommendation: str | None`、`strategies: str | None`
- `market: str | None`（"上市" / "上櫃" / "ETF" / "其他"）

#### RadarStrategy
- `name: str`、`description: str`、`hit_count: int`、`stocks_only: bool`

#### WatchlistMover (StockRef)
- `close, change_pct, composite_score: float | None`、`market: str | None`

#### WatchlistOverviewRow (StockRef)
- `close, change_pct, short, mid, long, composite: float | None`
- `recommendation, as_of, market: str | None`

#### ExDividendEvent (StockRef)
- `ex_date: str`、`cash_dividend, stock_dividend: float | None`
- `in_holdings, in_watchlist: bool`

#### ExDividendCalendarEvent (StockRef)
- `ex_date: str`、`cum_price, ex_price, dividend_value, yield_pct: float | None`
- `event_type: str | None`（"權" / "息" / "權/息"）
- `in_holdings, in_watchlist: bool`

#### HistoryPerfRow (StockRef)
- `snapshot_close, latest_close, change_pct, short, mid, long, composite: float | None`
- `recommendation, strategies, latest_date: str | None`

#### HistoryPerfSummary
- `as_of: str`、`latest_date: str | None`、`days_elapsed: int`
- `hit_count, win_count, loss_count: int`
- `win_rate, avg_change_pct: float | None`
- `rows: list[HistoryPerfRow]`

#### DataFreshness
- `table, label: str`、`latest_date: str | None`
- `lag_days: int | None`、`tone: str`（"ok" / "warning" / "error"）

---

### Portfolio — [api/schemas/portfolio.py](../api/schemas/portfolio.py)

#### PortfolioSummary
- `total_market_value, total_cost, unrealized_pnl, net_unrealized_pnl, estimated_sell_costs, today_pnl: float`
- `unrealized_pnl_pct, net_unrealized_pnl_pct, today_pnl_pct: float | None`
- `holding_count: int`

#### HoldingRow (StockRef)
- `shares, avg_cost: float`
- `price, prev_close, today_pct, market_value, unrealized_pnl, unrealized_pnl_pct, net_unrealized_pnl, net_unrealized_pnl_pct, estimated_sell_costs: float | None`
- `short_score, mid_score, long_score, composite_score: float | None`
- `warnings: list[str]`

#### RiskAlert
- `severity: str`（"info" / "warning" / "critical"）
- `title, description: str`
- `stock_id: str | None`

#### TradeRow (StockRefOptional)
- `id: int`、`trade_date, action: str`、`shares, price: float`
- `fee, tax: float | None`、`note: str | None`

#### RealizedPnlRow (StockRefOptional)
- `buy_date, sell_date: str`
- `shares, buy_price, sell_price, cost, proceed, pnl: float`
- `pnl_pct: float | None`

#### RealizedPnlSummary
- `total_pnl: float`、`pair_count, win_count: int`
- `win_rate: float | None`、`rows: list[RealizedPnlRow]`

---

### Market — [api/schemas/market.py](../api/schemas/market.py)

#### MarketSnapshot
- `date: str | None`、`close, change_pct: float | None`

#### MarketBreadth
- `n_total, n_up, n_down, n_unchanged, n_new_high_50d, n_new_low_50d: int`
- `advance_decline_ratio, pct_above_ma20, pct_above_ma60, new_high_low_ratio: float | None`
- `health_label: str | None`、`health_tone: str`

#### IndustryRotationResponse
- `as_of: str | None`（YYYY-MM-DD，daily_price 全表最新日期）
- `rows: list[IndustryRotationRow]`

#### IndustryRotationRow
- `industry: str`、`n_members: int`
- `ret_1d, ret_5d, ret_20d, ret_60d, heat: float | None`（等權；給排行表）
- `ret_1d_weighted: float | None`（成交值加權當日報酬；給熱力圖著色）
- `total_amount: float | None`（該產業最新交易日成交金額總和，TWD；給熱力圖磚塊面積）
- `n_up: int`、`n_flat: int`、`n_down: int`（當日 ret_1d 正/零/負 的成員家數，給熱力圖 hover 卡片）

#### IndustryMemberRow
- `stock_id, stock_name: str`
- `close, ret_1d, ret_5d, ret_20d: float | None`

---

### Backtest — [api/schemas/backtest.py](../api/schemas/backtest.py)

#### BacktestConfig
- `entry_threshold: float = 65.0`、`exit_threshold: float = 40.0`
- `stop_loss_pct: float = 0.08`、`take_profit_pct: float = 0.20`
- `max_hold_days: int = 60`、`slippage_bps: float = 5.0`
- `fee_rate: float | None`（None = 用 config.yaml 折扣）、`tax_rate: float = 0.003`
- `lookback_days: int = 500`、`use_adj: bool = True`
- ATR 動態停利（Chandelier-style，預設 off 維持向後相容）
  - `trailing_tp_mode: "off" | "both" | "only" = "off"`
  - `trailing_tp_atr_multiplier: float = 3.0`（K，停損用 2.0；停利給趨勢更多呼吸空間）
  - `trailing_tp_arm_pnl: float = 0.08`（浮盈 ≥ 8% 才啟動）
  - `trailing_tp_arm_days: int = 5`（持有 ≥ 5 日才啟動）
  - `trailing_tp_atr_period: int = 14`

#### BacktestRequest
- `stock_id: str`、`config: BacktestConfig | None`

#### BacktestTrade
- `entry_date, exit_date: str`、`hold_days: int`
- `entry_price, exit_price, gross_return, net_return: float`
- `exit_reason: str`（"stop_loss" / "trailing_take_profit" / "take_profit" / "score_exit" / "max_hold"）
  - 優先序（先觸發先出）：`stop_loss` > `trailing_take_profit` > `take_profit` > `score_exit` > `max_hold`

#### BacktestDailyPoint
- `date: str`、`close, short_score: float | None`

#### BacktestSummary (StockRefOptional)
- `n_trades: int`、`win_rate, avg_return, total_return, max_drawdown, buy_and_hold, alpha: float`

#### BacktestResponse
- `summary: BacktestSummary`、`trades: list[BacktestTrade]`、`daily_series: list[BacktestDailyPoint]`、`config: BacktestConfig`

#### PortfolioBacktestRequest / PortfolioRow / PortfolioAggregate / PortfolioBacktestResponse
- 多股版本，含 `alpha_vs_0050` / `alpha_vs_taiex`、benchmark return

#### GridSearchRequest / GridSearchRow / GridSearchResponse
- `entry_list, exit_list, sl_list, tp_list: list[float]`、`combos: int`、`best: GridSearchRow | None`、`elapsed_sec: float`

#### WalkForwardRequest / WalkForwardSplitRow / WalkForwardResponse
- `n_splits: int = 3`、`train_ratio: float = 0.7`
- 回應含 `avg_train_return`、`avg_test_return`、`overfit_warning: bool`、`note?`

#### EventBacktestRequest / EventTradeRow / StockEventStatsRow / EventBacktestSummary / EventBacktestResponse
- `entry_offset: int = -5`、`exit_offset: int = 10`、`since_year: int = 2020`、`min_dividend: float = 0.5`
- 回應含 `summary` / `by_stock[]` / `trades[]` / `config_echo`

---

### Inline schemas（各 router 內定義）

#### portfolio.TradeCreateBody（[portfolio.py:27](../api/routers/portfolio.py#L27)）
- `trade_date: str` (YYYY-MM-DD)、`stock_id: str`
- `action: Literal["BUY", "SELL"]`
- `shares: float (>0)`、`price: float (>0)`
- `fee, tax: float | None (>=0)`、`note: str | None`

#### watchlist.WatchlistEntry / WatchlistLookup / AddBody / BulkAddBody / BulkRemoveBody
- 簡單 wrapper，見 [watchlist.py:19-43](../api/routers/watchlist.py#L19)

#### dq.PriceAnomaly / StockGap / DqSummary（[dq.py:27-53](../api/routers/dq.py#L27)）
- `PriceAnomaly`: stockId, stockName, market?, kind, severity, date, value?, note
- `StockGap`: stockId, stockName, table, missingDays, expected
- `DqSummary`: asOf?, nAnomalies, nGaps, anomalies[], gaps[], scope

#### search.SearchHit（[search.py:18](../api/routers/search.py#L18)）
- `stock_id, stock_name: str`、`market, industry: str | None`、`in_watchlist: bool`

#### weight_tuner — DefaultWeights / StockBreakdown / TunerBreakdownResponse / WeightSet / BuiltinPreset / UserPreset / PresetListResponse / PresetUpsertRequest / VisibleKeysResponse
- 見 [weight_tuner.py:28-122](../api/routers/weight_tuner.py#L28)

---

## App-layer 函式 → API endpoint 對照

> 已包成 endpoint 的標 ✅；未接的標 ⚠️（補功能 TODO）；內部 helper / 不適合 API 的標 — 不列。

### app/portfolio.py — [app/portfolio.py](../app/portfolio.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `effective_fee(shares, price)` | — | 內部 helper（POST /trades 自動套用） |
| `list_holdings(db)` | ✅ | GET /api/portfolio/holdings |
| `get_holding(db, sid)` | ⚠️ | 未接 — 目前 holdings 回傳整個列表，沒有「單檔細項」endpoint |
| `record_trade(...)` | ✅ | POST /api/portfolio/trades |
| `delete_trade(db, id)` | ✅ | DELETE /api/portfolio/trades/{id} |
| `rebuild_holding(db, sid)` | ✅ | POST /api/system/rebuild-holding/{stock_id} |
| `load_trades(db, sid?)` | ✅ | GET /api/portfolio/trades（支援 `?stock_id=` 過濾） |
| `realized_pnl(db, sid?)` | ✅ | GET /api/portfolio/realized-pnl（支援 `?stock_id=` 過濾） |
| `risk_signals(db, h, close, score)` | — | 已被 `enhanced_risk_signals` 取代，holdings router 直接用後者 |

### app/watchlist.py — [app/watchlist.py](../app/watchlist.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `load()` | ✅ | GET /api/watchlist |
| `save(stocks)` | — | 內部 helper |
| `add(sid, name)` | ✅ | POST /api/watchlist |
| `add_many(items)` | ✅ | POST /api/watchlist/bulk-add |
| `remove(sid)` | ✅ | DELETE /api/watchlist/{stock_id} |
| `remove_many(sids)` | ✅ | POST /api/watchlist/bulk-remove |
| `contains(sid)` | — | 由 lookup 隱含覆蓋 |

### app/scoring/engine.py — [app/scoring/engine.py](../app/scoring/engine.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `score_stock(db, sid, name)` | ✅ | GET /api/stocks/{id}/score、weight_tuner/breakdown |
| `score_short_term / score_mid_term / score_long_term` | — | 內部，由 score_stock 組合 |
| `composite_score / overall_completeness / build_signals` | — | 內部 |
| `recommendation_label(score)` | — | 內部，已隱含於 StockScoreView.recommendation |
| `check_stale(as_of)` | — | 內部 |

### app/scoring/history.py — [app/scoring/history.py](../app/scoring/history.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `snapshot_today(db)` | ✅ | POST /api/system/refresh-snapshot（手動強制重跑） |
| `load_snapshot(db, as_of)` | ✅ | history/performance 內部使用 |
| `available_dates(db)` | ✅ | GET /api/history/dates |
| `track_performance(db, as_of, strategy?)` | ✅ | GET /api/history/performance |

### app/scoring/radar.py — [app/scoring/radar.py](../app/scoring/radar.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `STRATEGIES` (dict) | ✅ | radar/strategies、history/strategies |
| `list_candidate_stocks(db)` | — | 內部 |
| `score_all(db, ...)` | ⚠️ | 未接 — 是 radar 的核心批次評分函式（產 signal_history），目前只能透過 CLI / cron 跑；沒有 API endpoint 讓使用者「即時掃整個市場」 |
| `scan(db, name, top_n)` | ⚠️ | 未接 — 「指定策略 + 重新計算」的入口；目前 /radar/hits 是讀現成 signal_history |

### app/scoring/radar_queries.py — [app/scoring/radar_queries.py](../app/scoring/radar_queries.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `latest_as_of(db)` | ✅ | radar/strategies 內部使用 |
| `query_radar_hits(...)` | ✅ | radar/hits、dashboard/radar-hits |

### app/scoring/snapshot_freshness.py — [app/scoring/snapshot_freshness.py](../app/scoring/snapshot_freshness.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `freshness_status(db)` | ✅ | GET /api/system/snapshot-status（含 `stale_reason`、`can_refresh`、`engine_version_*`） |
| `is_stale(db)` | ✅ | 內部 helper（`ensure_fresh` 與 freshness 檢查共用） |
| `ensure_fresh(db)` | — | 列表 router 內部呼叫 |

### app/scoring/preset.py — [app/scoring/preset.py](../app/scoring/preset.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `list_presets(db)` | ✅ | GET /api/weight-tuner/presets |
| `get_preset(db, name)` | ⚠️ | 未接 — 目前 /presets 一次回所有，沒有「取單一 preset 詳情」endpoint |
| `upsert_preset(db, ...)` | ✅ | POST /api/weight-tuner/presets |
| `delete_preset(db, name)` | ✅ | DELETE /api/weight-tuner/presets/{name} |
| `builtin_presets()` | ✅ | GET /api/weight-tuner/presets |

### app/scoring/radar_cache.py — [app/scoring/radar_cache.py](../app/scoring/radar_cache.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `load / save / _cleanup_stale` | — | 純 disk 快取 helper，由 score_all 自動使用 |

### app/scoring/rubric.py — [app/scoring/rubric.py](../app/scoring/rubric.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `score_*`（單項評分） | — | 內部 building blocks |
| `BEGINNER_VISIBLE_KEYS` (constant) | ✅ | GET /api/weight-tuner/presets/visible-keys |
| `SHORT/MID/LONG_TERM_WEIGHTS` (constants) | ✅ | GET /api/weight-tuner/breakdown |

### app/risk.py — [app/risk.py](../app/risk.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `compute_atr(price_df)` | — | 內部 helper |
| `atr_stop_loss(...)` | ✅ | GET /api/stocks/{id}/atr-stop（fixed 段） |
| `trailing_atr_stop(...)` | ✅ | GET /api/stocks/{id}/atr-stop（trailing 段） |
| `trailing_atr_take_profit(...)` | ✅ | GET /api/stocks/{id}/atr-stop（take_profit 段，Chandelier-style）；enhanced_risk_signals 內部使用 |
| `suggest_position_size(...)` | ⚠️ | 未接 — 「該買幾張」建議；買股前的 sizing helper，未透過 API 暴露 |
| `concentration_warnings(db, mv_dict)` | ✅ | GET /api/portfolio/risk-alerts 內部使用 |
| `enhanced_risk_signals(db, sid, ...)` | ✅ | GET /api/portfolio/holdings 內部使用 |

### app/backtest/engine.py — [app/backtest/engine.py](../app/backtest/engine.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `backtest_stock(db, sid, cfg, ...)` | ✅ | POST /api/backtest/stock |
| `backtest_portfolio(db, sids, cfg)` | ✅ | POST /api/backtest/portfolio、POST /api/backtest/grid-search |
| `walk_forward(db, sids, grid, ...)` | ✅ | POST /api/backtest/walk-forward |
| `benchmark_return(db, start, end, source)` | ✅ | backtest/portfolio 內部使用 |
| `with_benchmarks(...)` | ✅ | backtest/portfolio 內部使用 |
| `portfolio_summary(summaries)` | ✅ | backtest/portfolio、grid-search 內部使用 |

### app/backtest/event_driven.py — [app/backtest/event_driven.py](../app/backtest/event_driven.py)

| Function | 狀態 | 對應 endpoint |
|---|---|---|
| `run_event_backtest(db, sids, cfg)` | ✅ | POST /api/backtest/event-driven |

### app/indicators/* — [app/indicators/](../app/indicators/)

| 模組 / Function | 狀態 | 對應 endpoint |
|---|---|---|
| `technical.enrich(df)` 與所有 sma/ema/rsi/kd/macd/bollinger | ✅ | GET /api/stocks/{id}/price 內部使用 |
| `chips.*`（institutional/margin enrich） | — | 內部，由 score_stock 用 |
| `fundamentals.fundamental_snapshot(...)` | — | 內部 |
| `market_context.compute_rs(...)` | — | 內部評分用 |
| `market_scope.market_breadth(db)` | ✅ | GET /api/market/breadth |
| `market_scope.breadth_health_label(b)` | ✅ | GET /api/market/breadth |
| `market_scope.industry_rotation(...)` | ✅ | GET /api/market/industry-rotation |
| `market_scope.industry_members(...)` | ✅ | GET /api/market/industry-members |

### app/data/* — 資料更新管線（CLI / cron 使用，多數不適合 API）

| 模組 / Function | 狀態 | 備註 |
|---|---|---|
| `data.updater.DataUpdater` | ⚠️ | 未接 — 只能透過 CLI / scripts；考慮 `/api/admin/update?source=...` 但要做 auth |
| `data.market_updater.MarketUpdater` | ⚠️ | 未接 — 同上 |
| `data.adjuster.update_stock_adjusted(...)` | ⚠️ | 未接 — 個股還原股價更新 |
| `data.fetcher.FinMindFetcher` 系列 | — | 不適合 API（要金鑰、長時間） |
| `data.twse_fetcher.TwseFetcher.upcoming_dividends` | ✅ | GET /api/calendar/ex-dividend 內部使用 |
| `data.market_type.classify_market` | — | 內部 helper |
| `data.clock.taipei_today / taipei_now` | — | 內部 helper |
| `data.mops_fetcher / mops_financials_fetcher` | — | 資料抓取，不適合 API |

### app/notifier.py、app/report.py、app/run_log.py、app/backup.py、app/config.py

| 模組 | 狀態 | 備註 |
|---|---|---|
| `notifier.notify(message)` | ⚠️ | 未接 — Discord 通知；可考慮 `/api/admin/notify-test` 給設定頁驗證 |
| `report.build_report / generate_daily_report` | ⚠️ | 未接 — 每日報告產生器；考慮 `/api/report/daily?as_of=...` 讓前端下載 markdown |
| `run_log.start_run / finish_run / run_context` | ⚠️ | 未接 — 排程執行記錄；可接 `/api/system/run-log?limit=...` 監看背景任務 |
| `backup.run_daily_backup` | ⚠️ | 未接 — DB 備份；可接 `/api/admin/backup-now` 給手動觸發 |
| `config.Config` | — | 內部設定載入 |

---

## TODO（未接 API 的功能匯總）

> **2026-04-26 更新**：14 條 TODO 已完成 12 條（✅）。剩下 2 條 destructive endpoints（會跑很久且寫資料）暫不開放，等用戶決定。

| # | Endpoint | 狀態 | 備註 |
|---|---|---|---|
| 1 | `GET /api/system/snapshot-status` | ✅ | 在 [routers/system.py](../api/routers/system.py) |
| 2 | `POST /api/system/refresh-snapshot` | ✅ | 同上（路徑改在 `/system/`） |
| 3 | `GET /api/stocks/{id}/atr-stop` | ✅ | 含 fixed / trailing / take_profit 三段（停損 + Chandelier 動態停利），[routers/stocks.py](../api/routers/stocks.py) |
| 4 | `POST /api/portfolio/position-suggest` | ✅ | [routers/portfolio.py](../api/routers/portfolio.py) |
| 5 | `GET /api/portfolio/realized-pnl?stock_id=...` | ✅ | 既有 endpoint 加 query param |
| 6 | `GET /api/portfolio/trades?stock_id=...` | ✅ | 既有 endpoint 加 query param |
| 7 | `POST /api/system/rebuild-holding/{stock_id}` | ✅ | [routers/system.py](../api/routers/system.py) |
| 8 | `GET /api/system/report/daily?as_of=...` | ✅ | 直接讀 reports/*.md（不重跑 build_report） |
| 9 | `POST /api/system/notify-test` | ✅ | 帶 `message` body（可選），預設用樣板字串 |
| 10 | `GET /api/system/run-log?limit=20` | ✅ | 表不存在時回 [] |
| 11 | `POST /api/admin/update?source=...` | ⚠️ 暫不開放 | DataUpdater 跑全市場 1-2 分鐘，會寫資料；需先做 auth + 排隊機制再開 |
| 12 | `POST /api/system/backup-now` | ✅ | 包 `backup.run_daily_backup`；未啟用會回 triggered=false |
| 13 | `POST /api/radar/scan-now` | ⚠️ 暫不開放 | score_all 全市場 30 秒~1 分；用戶可以等 cron / 手動跑 `python -m scripts.market_update` |
| 14 | `GET /api/weight-tuner/presets/{name}` | ✅ | 在 [routers/weight_tuner.py](../api/routers/weight_tuner.py) |

**新增的 router**：[api/routers/system.py](../api/routers/system.py) — 集中管理系統狀態 / admin 工具，prefix `/api/system`，已註冊到 [api/main.py](../api/main.py)。
