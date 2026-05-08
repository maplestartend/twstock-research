# 台股自用研究系統 — 使用文件

> 自用、自研、免費。資料抓自證交所/櫃買中心 + FinMind 免費版。
>
> **延伸文件**：[架構與限制](docs/architecture.md) ・ [維運手冊](docs/operations.md) ・ [後端 API 規格](docs/api-spec.md) ・ [前端規格](docs/frontend-spec.md)

---

## 目錄

- [快速開始](#快速開始)
- [日常使用流程](#日常使用流程)
- [全域快捷鍵](#全域快捷鍵)
- [頁面用法](#頁面用法)
- [指令列腳本](#指令列腳本)
- [疑難排解](#疑難排解)

---

## 快速開始

### 一次性設定（電腦初次使用）

```bash
# 1. 安裝相依套件
pip install -r requirements.txt

# 2. 設定 FinMind token（二選一）
#    (a) 改 config.yaml 的 finmind.token（已 .gitignore）
#    (b) 設環境變數 FINMIND_TOKEN（優先度高於 yaml）
#    申請：https://finmindtrade.com/

# 3. 首次歷史回補（一次性，~30 分鐘）
python -m scripts.market_update --days 260

# 4. 自選股還原價（~1 分鐘）
python -m scripts.update_adj

# 5. 全市場產業別（~3 秒，日後偶爾跑）
python -m scripts.refresh_industry

# 6. 全市場最新月營收（~2 秒）
# 已接到 daily-update.bat 自動跑，不需手動；月初 1~10 號 OpenAPI 仍是上月資料，11 號後換成當月。
python -m scripts.update_monthly_revenue --mops

# 7. 一次回補近 5 季全市場財報（~15 秒，新季公告後跑）
python -m scripts.backfill_financials_history --quarters 5
```

### 啟動儀表板

**雙擊 `.bat`（推薦）**：

| 檔案 | 用途 |
|------|------|
| `launch.bat` | 啟動 FastAPI:8000 + Next.js:3000 + 開瀏覽器 |
| `stop.bat` | 一鍵關閉兩個 server（用 port 找 PID + 收 cmd 視窗，跑完驗收 port 已釋出） |
| `restart.bat` | stop → 重生 `signal_history` snapshot → relaunch。**改完 `app/scoring/*` 用這個** |
| `status.bat` | 不動 server，只看 port 8000 / 3000 在不在跑、snapshot 有沒有對齊 daily_price |
| `daily-update.bat` | 只抓當日資料（不開 UI），給排程跑 |
| `install-schedule.bat` | 安裝 Windows 工作排程，每日 15:30 自動 daily-update |
| `uninstall-schedule.bat` | 移除排程 |
| `check-holdings.bat [本金]` | 盤後一鍵檢查持股：列出 ATR 停損 / 結構警戒線 / Chandelier 鎖利狀態與隔日動作清單。本金預設 760,000，可帶參數覆蓋 |

> **找不到 uvicorn / next dev 在哪？** 不必翻 cmd 視窗，雙擊 `stop.bat` 就好（用 port 找 PID）。
>
> **常用順序（改完策略/評分邏輯後）**：先 `status.bat` 看 snapshot 對齊狀態 → 跑 `restart.bat` 強制重算當日 snapshot → 再用 `status.bat` 確認重算成功。

> 排程記得到「工作排程器 → TWStockDailyUpdate → 內容」勾「失敗 30 分鐘重試最多 3 次」與「執行工作時喚醒電腦」，筆電睡著也能補跑。

**指令列方式**：

```bash
# Terminal 1
.venv\Scripts\python.exe -m uvicorn api.main:app --reload --port 8000

# Terminal 2（首次需先 cd web && npm install）
cd web && npm run dev
```

瀏覽器：`http://localhost:3000`

### 深連結（URL 書籤）

每頁有獨立 URL：`/`（戰情室）、`/stocks/{id}`、`/radar?strategy=...&type=etf`、`/sectors?industry=...`、`/watchlist`、`/holdings`、`/dividend-calendar`、`/history`、`/watchlist-manage`、`/lab`（回測工具室）、`/backtest`、`/portfolio-backtest`、`/event-backtest`、`/grid-search`、`/weight-tuner`、`/dq`。

> 側欄 8 項分 4 區塊：概覽 / 持股 / 訊號 / 進階分析。`/watchlist-manage`、`/dividend-calendar` 從 `/watchlist` 進入；5 個回測/調優工具從 `/lab` 進入。所有 URL 仍可直接書籤。

> 技術棧、目錄、資料庫結構、注意事項與限制 → 詳見 [docs/architecture.md](docs/architecture.md)

---

## 日常使用流程

每天盤後（15:00 後，實際資料 16:30 才上線）：

```bash
# 1. 更新全市場 + 拍訊號快照（~1 分鐘）
python -m scripts.market_update

# 加 --push 推播早報、失敗、警告到 Discord
python -m scripts.market_update --push

# 2. 開儀表板（或雙擊 launch.bat）
```

### 不常做但要記得的事

```bash
# 加新自選股或更新配息
python -m scripts.update_adj

# 單檔財報深度補完（FinMind）
python -m scripts.daily_update --stock 2330

# 全市場最新季財報（已整合進 market_update，剛公告新季時手動跑加快）
# 注意：TWSE OpenAPI 是 conservative 模式，5/15 之前不會切到 Q1。要早抓用下面那條。
python -m scripts.update_financials_mops

# 早期公告者（5/15 之前的 Q1）— 可指定季別直接打 MOPS
# 已接到 daily-update.bat 自動跑，不需手動，僅在排程斷掉時補跑
python -m scripts.refresh_recent_financials

# 全市場歷史季財報回補（手動回補多季用）
python -m scripts.backfill_financials_history --quarters 8
```

> 推播設定、備份、Observability、測試 → 詳見 [docs/operations.md](docs/operations.md)

---

## 全域快捷鍵

| 快捷鍵 | 功能 |
|--------|------|
| <kbd>Ctrl</kbd>+<kbd>K</kbd> / <kbd>⌘</kbd>+<kbd>K</kbd> | 開啟全域搜尋 |
| <kbd>Esc</kbd> | 關閉搜尋 |
| <kbd>↑</kbd>/<kbd>↓</kbd> | 移動 |
| <kbd>Enter</kbd> | 確認 |

搜尋面板支援：代號（`2330`）、名稱（`台積`）、頁面關鍵字（`回測`/`族群`），自選股置頂並標星。也可在 Topbar 點「搜尋…」按鈕。

---

## 頁面用法

> 各頁的 UI 結構、API 呼叫、用到的元件詳見 [docs/frontend-spec.md](docs/frontend-spec.md)。下面只列重點。

### 🏠 今日戰情室（預設頁）
全部讀 DB，秒開。包含：大盤體質燈號、**🆕 KPI 條（持股總市值 / 今日損益 / 累積未實現損益 跟著盤中即時報價 30s 重算；雷達命中 / 資料狀態 server 端產出）**、💼 **持股明細**（與「我的持股」頁同一個 LiveHoldingsTable 元件、ATR 停損 + ATR 停利 badge、盤中即時報價 30s 輪詢）、⭐ 自選股 Top/Bottom 5、🎯 雷達命中 Top 10、📅 5 日除權息、🔧 資料更新狀態。

> KPI 與持股表共用同一份 server-prefetch 即時報價（React `cache` dedupe，同 request 內只打一次 mis 批次），第一次 render 就是即時值，沒有「先昨收後跳即時」閃爍。

> 持股「未實現損益」顯示**淨值**（毛 − 預估賣出手續費 − 證交稅）。**證交稅依代號自動分流**：一般股 0.3%、股票型 ETF 0.1%、債券 ETF 0%。
> 集中度提醒：單檔 >25% 或單一產業 >40%。同 severity 的多筆風險合併成 list 卡（避免 4 張 2x2 噪音）。

> **🆕 Topbar Snapshot 新鮮度指示器**（右上）：綠勾「快照最新」/ 黃 badge「需重算」+ 一鍵觸發 `/api/system/refresh-snapshot`。改完 `app/scoring/*` 後不必跑 `restart.bat` 也能在 UI 直接重生 snapshot。

### 📈 族群輪動
44 個產業多時間窗等權平均報酬，可切 1/5/20/60 日。**🆕 產業熱力圖升為 hero**（畫面最上方 480px+，treemap 是這頁的 headline insight）：Goodinfo 風格，**磚塊面積 = 該產業最新交易日成交值占大盤比**、**顏色 = 成交值加權當日漲跌**（紅漲綠跌、固定 ±10% 共 11 階離散色帶），字級依磚面積動態縮放；hover 出卡片顯示「當日加權報酬 / 上漲・持平・下跌家數 / 成交值 / 大盤占比」，點磚直接 drill-down 進該產業。

下方接「產業熱度排行表」：**點任一產業**進入該產業專屬頁，畫面只留該產業成員股近期表現；頂端**麵包屑**（族群輪動 › {產業}）可一鍵點回上一層。

### 📊 自選股總覽
個股 / ETF tab 分流（評分機制不同；ETF tab 隱藏「長期」欄）。顯示短/中/長/綜合，依綜合分降序，下方 Top 3 / Bottom 3。
**🆕 標籤分組**：tab 列下方多一排 tag filter chip（如「長期持有」「配息」「短線」），點擊只顯示帶該 tag 的檔；切 type tab 時保留 tag。每列代號旁也會渲染該檔的 tag chips 方便辨識。tag 在 `/watchlist-manage` 編輯。

### 🔍 個股詳情（決策工作台）
- **🆕 標頭即時報價**：股票名稱右側的大字價格、漲跌% 跟著 30 秒輪詢 `/api/stocks/{id}/intraday`（盤後 2 分鐘、tab 隱藏暫停）；mis 撈不到（興櫃 / 休市）退回昨日收盤
- **🆕 ATR 動態出場區塊「現價」即時化**：固定式 / 追蹤式停損、Chandelier 動態停利的「距 X% / 已破 / 建議出場」狀態都用即時價重判；不必等盤後才看到「現在跌穿停損沒」
- K 線圖（可勾「使用還原價」）
- **🆕 評分模式切換**：頂端三按鈕「收盤 / 即時 / 假設」
  - **收盤**（預設）：依昨日收盤算出的分數（雷達/自選看到的也是這個版本）
  - **即時**：抓 TWSE mis 盤中報價當作最新 close 重算 → 短/中分數反映盤中實況，每 30 秒自動刷新
  - **假設**：自己輸入「假設成交價」(±10% 滑桿)，秒級看到該價位下的分數變化；用來決定「跌到 X 才進場 vs 漲到 X 該不該追」
  - 長期分數固定不動（吃 ROE/EPS/股利等財報指標，盤中價無關）；KPI 卡會顯示與收盤分數的 Δ 差
- 評分拆解（短/中/長 三 tab，每個子項分數）
- 進出場建議、風險提示
- **🆕 同業比較區塊**：個股 vs 同產業中位數的 7 個指標（本益比 / 殖利率 / 毛利率 / EPS YoY / 營收 YoY / 負債比 / 流動比），每列以 horizontal bar 視覺化兩者相對位置 + 「#rank/N」徽章排名。ETF / 興櫃 / 同業 < 5 檔自動隱藏。資料源：`per_pbr` / `monthly_revenue` / `financials_quarterly_derived` / `financials_cumulative` 既有 cache，**不重跑** `fundamental_snapshot`，~30ms 完成
- 籌碼/基本面明細（可折疊）
- 📈 月營收柱狀 + YoY 折線
- 📉 近 90 天分數折線 + 預設策略近 1 年勝率/Alpha/B&H 快照
- 🗓️ 近期除權息 / 分割
- ⚙️ 一鍵跳「策略回測 / 投組回測 / 部位試算」

> 「即時」模式只影響個股詳情頁，**不會寫入 signal_history**（避免污染回測來源）。雷達/自選列表頁仍顯示昨日收盤分數。

### 💼 我的持股
單頁分區：KPI 總覽 / 持股明細 / 風險提醒 / 已實現損益 / 交易紀錄。
- **新增交易**：表單填 日期 / 代號 / 買賣 / 張數 / 成交價 / 備註，手續費（0.1425%）與證交稅（賣方 0.3%）後端自動算
- **刪除交易**：每列右側「刪除」鈕，二次確認後以 trade_log 重建該股 holdings
- **加入/移除自選**：每列代號左側 ⭐，點一下即 toggle（樂觀更新 + router.refresh）
- **已實現損益**：FIFO 配對
- **🆕 盤中即時報價**：持股明細 + KPI 摘要會 30 秒輪詢 `/api/portfolio/holdings/intraday`（盤後 2 分鐘、tab 隱藏暫停），現價 / 今日% / 市值 / 未實現損益 / ATR 停損距離 / ATR 停利距離全部跟著即時變動。標頭顯示「即時 N/M」徽章；mis 撈不到（興櫃 / 休市）的 row 退回昨日收盤
- **🆕 ATR 停利欄**：Chandelier 3×ATR 動態停利（進場後高點 − 3×ATR）。需「進場日 + 浮盈 ≥ 8% + 持有 ≥ 5 日」才啟動；觸發顯示「建議出場」紅字。短/中/長 評分欄已從表格移除（請從個股詳情頁查看）
- **下載 Excel**：「持股明細」標題列右側「下載 Excel」鈕，匯出含 brand-color 表頭、凍結 A:B 兩欄、CJK 自動欄寬的 .xlsx；資料與螢幕上同份（含 ATR 停損 / ATR 停利欄）

### 🎯 雷達掃描
個股 / ETF tab 分流（ETF 識別：代號 `00` 開頭且長度 ≥ 4）。預設策略 = 短線強勢。
**內建策略**：短線強勢、中期波段、長期價值、外資連買（≥5 日 + 20 日淨買超）、回檔布局、三榜俱佳、相對強勢（20 日跑贏指數 >5%）、月營收爆發（YoY >20%）、營收持續成長（≥3 月 YoY>0）、營收高速加速（≥3 月 YoY>20%）。
過濾器：市場類型、顯示前 N 名、是否含基本面。可批次加入自選。命中表上方右側可「下載 CSV」（前端產出，含 BOM 對齊 Excel 繁中）或 **🆕 「下載 Excel」**（後端 openpyxl 產出，第一列含策略/市場/截止日/命中數 metadata、凍結代號名稱與表頭，預設帶全部命中而非只當前頁）。
> 首次掃描 ~20-30 秒（~2300 檔上市+上櫃），結果寫 `data/cache/radar_*.parquet`，DB 推進日期/月營收時自動失效。

### 📅 除權息行事曆
未來 7~90 天內要除權息的股票，三 tab：💼 庫存股 / ⭐ 自選股 / 🌐 全部。顯示除權息日、前收盤、參考價、權息值、**殖利率估算**。Tab 列右側可「下載 CSV」（依當前 tab 篩選結果）。

### 📜 歷史追蹤
回看某天雷達選出來的股票到今天的表現。個股 / ETF tab 分流。策略 chip 只顯示當前 tab 當日有命中的（hitCount=0 隱藏）。所有命中都列出，依綜合分降序，超過 50 檔自動分頁。

**🆕 資料累積中狀態**：當日命中後尚未經過任何交易日（`daysElapsed < 1`） → 顯示「資料累積中」骨架而非 0% / 0 天 KPI（避免第一眼像 bug）。

> 此頁與「分數走勢折線圖」讀**歷史快照**（回測用途）。雷達/自選/持股列表頁讀**最新快照**並透過 [snapshot_freshness](app/scoring/snapshot_freshness.py) 自動補跑 → 與詳情頁即時計算對齊。

### 📝 自選股管理
新增（代號→名稱由 stock_info 自動帶出）、批次貼上、勾選刪除。原子寫入 `watchlist.yaml`。
**🆕 標籤編輯**：每列「標籤」欄可直接編輯：chip 上點 × 移除、輸入框輸入新 tag 後 Enter / blur 加入。後端會自動 trim + 去重（保序）。空 list 視為清空。樂觀更新 + 失敗自動 rollback。watchlist.yaml 用 parallel `tags:` map 儲存，舊格式（只有 `stocks:`）仍可正常讀取。

### ⏮ 策略回測 / 📊 投組回測 / 🔬 參數掃描
- **🎨 情景預設**：頁首 3 卡（短線快手 / 波段獵人 / 長期持有），一鍵套用避免手調 7 個 slider
- **進階參數折疊**：停損/停利/持有天數/滑價默認收摺
- **一句話結論卡**：依 Alpha vs 0050 自動產生紅黃綠結論
- 投組回測：來源（自選 / 雷達 Top N / 自訂），滑價預設 5 bps，自動附 0050 / TAIEX B&H Alpha；每檔明細表可「下載 CSV」
- 參數掃描：3 卡「保守 8 / 平衡 24 / 激進 48 組」，Alpha 熱力圖 + 🧪 Walk-forward 驗證（train/test 切片偵測 overfit）
- **動態停利**（選用）：BacktestConfig 新增 `trailing_tp_mode`（off/both/only）、`trailing_tp_atr_multiplier`（K，預設 3.0）、`trailing_tp_arm_pnl`（armed 浮盈門檻，預設 8%）、`trailing_tp_arm_days`（armed 持有日門檻，預設 5）。Chandelier-style：peak_high − K×ATR；exit 優先序 stop_loss > trailing_take_profit > take_profit > score_exit > max_hold

### 🎉 除權息回測
事件驅動：每次除權息事件模擬「前 N 日進場、後 M 日出場」歷史報酬。3 預設「提前布局 / 經典套息 / 貼息回補」。報酬以**還原價優先**計算（`daily_price_adj.close_adj`，缺值才 fallback 原始 close），避免 split / 配股把績效誤算成假暴跌；資料源 `adj_event`，每次最多 100 檔。

### 🩺 資料品質
五類異常掃描：漲停/急漲、跌停/急跌（critical）、量爆（≥5×均量）、停滯（≥3 日不變）、跳空缺口（>15% 且查無 adj_event，疑漏抓還原；critical）。+ 缺值掃描（近 N 日缺超過 1/3）。窗口 5/10/20/60 日切換。各異常類型自動提示對應修補腳本。

### 📊 因子檢定
對 `signal_history` 已寫入的歷史快照算 forward-return Information Coefficient（Spearman 秩相關），檢驗 short / mid / long / composite / vr_macd 五個分數對 5 / 20 / 60 日後的報酬率有沒有預測力。forward return 以 **還原價優先**（`daily_price_adj.close_adj`）計算，缺值才 fallback 原始 close，降低除權息/分割對 IC 的污染。
- **IC heatmap**：紅 = 正向預測（IC > 0）、綠 = 反向、灰 = 無訊號（|IC| < 0.05）；強度分 weak / mid / strong 三級，> 0.10 算強
- **IC_IR**（mean / std）：跨期穩定度，> 0.5 算可信賴；< 0.3 表時好時壞，要警惕過擬合
- **Q5 − Q1 spread**：「買最強 20% / 賣最弱 20%」多空組合的平均 forward return
- 樣本不足（單日 < 30 檔 / 全期 < 5 個 IC 點）→ 回 — 而非假數字。第一次跑若 signal_history 太薄，先 `python -m scripts.backfill_signal_history --days 60`（含財報約 40~60 秒/天；`--no-fundamentals` 可再加速）

### ⚙️ 權重調優
19 子維度 slider（短 9 + 中 5 + 長 5）。即時看自選股**原分數 vs 新分數 + 差異**。
- **🎨 主題式預設**：6 組（default / conservative / growth / technical / chip / fundamental，定義在 [app/scoring/rubric.py](app/scoring/rubric.py) 的 `BUILTIN_WEIGHT_PRESETS`）
- **💾 我的 Preset**：命名儲存到 `user_weight_preset` 表，可刪除（內建名稱保留）
- **🎓 新手 / 進階模式**：新手只顯示每維度影響度最大 4-5 個指標（白名單 `BEGINNER_VISIBLE_KEYS`），進階顯示全部 19 個。模式偏好存 localStorage
- 純前端重算，怎麼拉都不會弄壞系統

---

## 指令列腳本

| 腳本 | 用途 | 典型耗時 |
|------|------|---------|
| `python -m scripts.market_update` | 全市場增量 + 還原價 + 訊號快照 + 早報 | 1~2 分鐘 |
| `python -m scripts.market_update --days 260` | 歷史回補 260 天 | 30~40 分鐘 |
| `python -m scripts.market_update --date 2026-04-22` | 單日補資料 | 11 秒 |
| `python -m scripts.market_update --no-snapshot --no-report --no-adj` | 只更新資料 | ~11 秒 |
| `python -m scripts.market_update --push` | 啟用推播（早報/失敗/警告） | 同上 + 1 秒 |
| `python -m scripts.update_adj` | 自選股還原（已整合進 market_update） | ~5 秒/檔 |
| `python -m scripts.update_adj --all-in-db` | 全市場還原（慢，不建議） | 數小時 |
| `python -m scripts.update_monthly_revenue` | watchlist + holdings 月營收（FinMind 逐檔） | ~1 秒/檔 |
| `python -m scripts.update_monthly_revenue --mops` | 全市場最新月（TWSE/TPEX OpenAPI）。**已接到 `daily-update.bat`** | ~2 秒 |
| `python -m scripts.update_monthly_revenue --mops --from 2022-01 --to 2026-03` | 全市場歷史回補（MOPS 舊版） | 51 月約 1 分鐘 |
| `python -m scripts.update_financials_mops` | 全市場最新季財報（綜損 + 資產負債） | ~10 秒 |
| `python -m scripts.update_financials_mops --income` / `--balance` | 只抓綜損 / 資產負債 | ~5 秒 |
| `python -m scripts.backfill_financials_history` | 全市場歷史季財報回補（預設 5 季，MOPS） | ~15 秒 |
| `python -m scripts.backfill_financials_history --quarters 8` | 8 季 | ~25 秒 |
| `python -m scripts.backfill_financials_history --skip-existing` | 跳過已抓季（cron 排程友善） | 視季數而定 |
| `python -m scripts.refresh_recent_financials` | **🆕 自動回補「公告期內」的最新季**（5/15、8/14、11/14、3/31 deadline 前 60 天 ~ 後 30 天）。已接到 `daily-update.bat`，每天盤後自動補早期公告者 | ~3 秒/季 |
| `python -m scripts.refresh_industry` | 補回 stock_info.industry_category | ~3 秒 |
| `python -m scripts.daily_update --stock 2330` | 個股財報深度補完（FinMind） | ~8 秒 |
| `python -m scripts.dq_check` / `--push` | 資料品質檢查 | ~1 秒 |
| `python -m scripts.run_stats` / `--tail 20` / `--show-errors` | 執行統計 | ~1 秒 |
| `python -m scripts.prune_signals` / `--dry-run` / `--keep 60` | signal_history 壓縮（近 90 天逐日 + 之前只留週一），偶爾跑控制 DB 體積 | ~1 秒 |
| `python -m scripts.backfill_signal_history --days 60` | 把 signal_history 回填 60 個交易日（給「因子檢定」頁吃）；改過 scoring 邏輯後加 `--clear` 先清舊算法的快照再重算。可搭配 `--skip-existing`（只補缺）或 `--no-fundamentals`（加速）；`--workers N` 並行（預設 4，i5-13400F 建議 6） | 含財報約 40~60 秒/天；workers=6 1000 天約 ~100 分鐘 |
| `python -m scripts.backfill_index_yfinance --from 2022-01-01` | 用 yfinance `^TWII` 補 TWSE 加權指數歷史（TWSE OpenAPI 只回 ~2.7 年，要更早只能靠 yfinance）。預設 `--skip-existing` 不覆寫，給 mid factor RS 因子用 | ~30 秒 |
| `python -m scripts.backfill_daily_price_yfinance` / `--apply` | 🆕 用 yfinance 補個股 OHLC 2022-2023（TWSE OpenAPI 同樣只回 ~2.7 年）。`--from 2022-01-01` 預設、`--skip-existing` 預設（只補比現有 MIN(date) 早的）；可指定 `--stocks 2330,2454,5483` debug | ~1-2 小時跑全市場 |
| `python -m scripts.prune_warrants` / `--apply` | 🆕 一次性清掉 daily_price/institutional/margin 裡的權證 + 孤兒列（白名單語意：留 stock_info.is_tradable=1 的）；`--apply` 才會真刪 + VACUUM。**VACUUM 需 exclusive lock，先 stop.bat 收掉服務** | DELETE 5-10 分、VACUUM 1-2 分 |

> `--push-line` 舊旗標仍相容（等同 `--push`），LINE Notify 已停服。

### 系統 / 管理 API（給工具腳本或 admin UI 用）

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/system/snapshot-status` | 比對 signal_history vs daily_price，回 `isStale`（Topbar 指示器讀這個） |
| POST | `/api/system/refresh-snapshot` | 強制重跑當日 signal_history（耗時 1-2 分鐘）；Topbar 黃 badge 點擊觸發 |
| GET | `/api/dashboard/snapshot-delta?top=N` | 戰情室「今日 vs 昨日」delta：新進命中 / 跌出命中 / 分數 \|Δ\|≥5 |
| POST | `/api/system/notify-test` | 送一則測試訊息驗 Discord webhook |
| GET | `/api/system/run-log?limit=20` | market_update 執行歷史（依 run_log 表） |
| POST | `/api/system/backup-now` | 手動觸發 DB 備份（需 config 啟用） |
| POST | `/api/system/rebuild-holding/{id}` | 以 trade_log 重建單檔 holdings |
| GET | `/api/system/report/daily?as_of=YYYY-MM-DD` | 讀現成 reports/*.md（無 as_of → latest） |
| GET | `/api/stocks/{id}/atr-stop?entry_date=...&entry_price=...` | ATR 進出場建議（fixed 停損 + trailing 停損 + Chandelier 動態停利）。停利 armed 條件：浮盈 ≥ 8% 且持有 ≥ 5 日；可調 `tp_multiplier`（預設 3.0）/ `tp_arm_pnl` / `tp_arm_days` |
| POST | `/api/portfolio/position-suggest` | 固定比例風險法的張數試算 |
| GET | `/api/portfolio/trades?stock_id=2330` | 單檔交易紀錄 |
| GET | `/api/portfolio/realized-pnl?stock_id=2330` | 單檔已實現損益 |
| GET | `/api/weight-tuner/presets/{name}` | 取單一 user preset 詳情 |

> 完整 API 規格 → [docs/api-spec.md](docs/api-spec.md)

---

## 疑難排解

### FastAPI 出現 `TypeError: ... got an unexpected keyword argument ...`
uvicorn `--reload` 沒抓到模組更新。FastAPI 視窗按 `Ctrl+C` 後重起 `launch.bat`。

### `market_update` 跑到一半掛掉
TWSE/TPEx 偶爾異常（凌晨維護）。再跑一次，有增量邏輯會接續。

### 雷達頁顯示 0 檔命中
1. 檢查 `data/stock.db` 存在
2. 跑 `python -m scripts.market_update --days 260` 補歷史
3. 或放寬市場篩選（你可能只勾了 ETF）

### 前端 `cd web && npm run dev` 起不來
1. 確認跑過 `cd web && npm install`
2. 確認 Node >= 20（`node -v`）
3. 確認 port 3000 沒被佔用

### FinMind 回 `Your level is register`
碰到付費端點。系統只用三個免費端點：`TaiwanStockFinancialStatements`、`TaiwanStockDividendResult`、`TaiwanStockSplitPrice`。其他腳本報這錯代表誤用付費端點，停下來。

### MOPS 回「FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED!」
MOPS POST 端點要 `Referer` header（已帶）。仍被擋通常是 IP 被臨時 rate-limit，等幾分鐘再跑。`market_update.py` 的偵測式回補只在缺資料時才打 MOPS，不會每天打。

### 非 watchlist 股票沒有長期分數
1. 跑 `python -m scripts.update_financials_mops`（補當季）
2. 再跑 `python -m scripts.backfill_financials_history --quarters 5`（補歷史）
3. 重啟 FastAPI 並重整頁面

ETF / 興櫃 / 衍生商品本來就無 TWSE 財報，long 永遠 None（合理）。

### 0050 歷史回測 B&H 看起來不對
沒跑 `update_adj`。跑一次即可（0050 2025 年有 1:4 分割）。
