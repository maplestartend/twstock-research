# 維運手冊

> 推播、備份、Observability、測試。日常使用見 [../USAGE.md](../USAGE.md)。

## 推播通知設定

由 [../app/notifier.py](../app/notifier.py) 抽象出的通知層，目前實作 Discord，未來可擴充 ntfy / Telegram / Email。
觸發點：`--push` 時送出早報、執行中止（top-level exception）、收工時的警告總結（WARNING+ 記錄）。

> LINE Notify 已於 2025/3/31 停止服務，原本的 `LINE_NOTIFY_TOKEN` 已不再有效，改用 Discord。

### Discord（推薦）

1. Discord 選一個頻道 → 頻道設定 → 整合 → 建立 Webhook → 複製 Webhook URL
2. 貼進 `config.yaml`：
   ```yaml
   notify:
     channel: discord
     discord:
       webhook_url: "https://discord.com/api/webhooks/..."
   ```
3. 跑時加 `--push`：
   ```bash
   python -m scripts.market_update --push
   ```

### 環境變數覆寫

- `NOTIFY_CHANNEL=none`：暫時關掉推播，不動 yaml
- `DISCORD_WEBHOOK_URL=...`：覆寫 yaml 裡的 webhook URL（換新 URL 但不想碰檔案）

### 新增其他管道

在 `app/notifier.py` 加 `_send_xxx(message, title, cfg)` 函式，主 `notify()` 函式多一個分支，config 改 `channel: xxx` 即可。無需碰其他檔案。

> 早報同時會存到 `reports/YYYY-MM-DD.md` 與 `reports/latest.md`，即使沒推播也有本地檔案可查。

## 備份（opt-in）

備份機制**預設關閉**。想啟用需兩步：

1. **找到 Google Drive 同步資料夾**：安裝 Google Drive for Desktop 後會出現 `C:\Users\<你>\Google Drive\My Drive\` 或 `G:\My Drive\` 路徑
2. **改 config.yaml** 的 `backup:` 區塊：
   ```yaml
   backup:
     enabled: true
     path: "G:/My Drive/台股備份"
     keep_days: 14
     keep_weeks: 8
     keep_months: 12
   ```

之後每次 `market_update` 跑完會自動用 `VACUUM INTO` 產生一份 `stock_YYYYMMDD.db`（原子且一致）到該資料夾，Google Drive 會自動上雲。保留規則：最近 14 天每日、每週一保留 8 週、每月 1 號保留 12 個月。

若 path 資料夾不存在會 fallback 到本機 `data/backup/`（並記 log 警告）。

## 歷史表保留 / VACUUM 維運

`signal_history`（~2k 列/天）與 `signal_history_factor_parts`（~48k 列/天 = 每檔 × 3 horizon × ~21 sub-factor）每天 `market_update` 都會寫入，**不清理就無限長大**——後者曾累積 ~3.7 年、~4.2 GB（占全庫 8.8 GB 的 81%）。

**保留策略（兩表共用）**：近 **365 天逐日** + 更早**只留週一**（壓縮率 ≈ 5x）。`/diagnostics` 預設 `lookback=120` 天完全落在逐日窗內 → 預設畫面不受影響；`lookback` 365~2000 的長回看在舊段退化為週一抽樣（cross-sectional IC 在稀疏日期下仍成立，只是 `n_dates` 變少）。

**自動執行**：`daily-update.bat` 每天盤後跑 `python -m scripts.prune_signals --vacuum-weekly`：
- 每天 **DELETE** 舊列（快、idempotent、WAL-safe），與當日抓取成敗無關（獨立 `RC5`，失敗只 WARN 不中斷）。
- **VACUUM 只在週日** best-effort：API server（uvicorn）持鎖時拿不到 exclusive lock → 記 WARN 跳過，free pages 留在檔內、下次成功 VACUUM 再回收。

> **刪 `signal_history_factor_parts` 必須清 `factor_ic_cache`**：IC cache key 只追蹤 `signal_history` 的 `MAX(as_of)`、不追蹤 factor_parts 內容；不清的話 `lookback > 365` 的 sub-factor IC 會 cache hit 回到 prune 前的舊結果。`prune_all()` 已在「真的刪到 parts 列」時自動 `DELETE FROM factor_ic_cache`。

**一次性清積壓（首次部署本功能時手動跑一次）**：首刪 ~40M+ 列是數分鐘的大 WAL 操作，別塞進 16:45 排程（dashboard 可能開著）。流程：
```bash
stop.bat                                          # 釋放 API server 的 DB 鎖
python -m scripts.prune_signals --dry-run         # 確認會刪多少
python -m scripts.prune_signals --vacuum          # 實刪兩表 + 清 cache + 完整 VACUUM 回收磁碟
python -m pytest tests/ -q                        # 驗證
launch.bat                                         # 重啟
```
> ⚠️ VACUUM 會寫整份暫存副本 → **先確認磁碟有 ~6 GB 以上空閒**。跑完後 DB 由 ~8.8 GB 降到 ~6 GB；此後每日增量 prune 只碰剛跨過 365 天界線的那一天，極小。VACUUM 與上面「備份」用的 `VACUUM INTO` 不同：前者就地縮小 `data/stock.db`，後者產生壓縮過的*副本*供雲端。

## 執行記錄 (Observability)

每次 `market_update` 執行都會記到 `run_log` 表（自動建）。查詢：

```bash
python -m scripts.run_stats                # 近 30 天統計（成功率、平均耗時、警告數）
python -m scripts.run_stats --tail 20      # 最近 20 次明細
python -m scripts.run_stats --show-errors  # 只看錯誤
```

用途：排程設了之後偶爾掃一眼，失敗率突升或平均耗時明顯變長就表示該 debug。

## 測試

核心純函式模組 + router 整合測試：

```bash
pip install pytest
python -m pytest tests/ -q
# 497 passed (2026-05-10)
```

覆蓋（重點）：
- `tests/test_risk.py`：ATR、部位計算、集中度
- `tests/test_adjuster.py`：還原價因子鏈
- `tests/test_rubric.py`：評分上下界、缺資料處理、方向性
- `tests/test_preset.py` / `test_preset_invariants.py`：權重 preset CRUD + 各 preset 加權和 = 1.0
- `tests/test_routers.py`：FastAPI router 整合（用 TestClient + 合成 SQLite），含 holdings ATR 欄位 schema 檢查
- `tests/test_backtest_engine.py`：策略迴圈純邏輯——漲跌停跳過 / 切片 flat-reset / Sharpe / 盤中暫態
- `tests/test_score_consistency.py`：score_stock 與 score_all 結果必須對齊（避免 fund_snap 路徑不同步）
- `tests/test_fundamentals_derived_path.py`：derived path 必須算 `eps_cagr_3y` / `peg` / 單季 YoY（防 2026-04-30 P0 bug regression）
- `tests/test_financials_publish_date.py`：FinMind 季財報法定下限 stamp + look-ahead 過濾
- `tests/test_market_updater_warrant_filter.py`：MarketUpdater 不再把 5 碼權證寫進 daily_price/institutional

改 `app/risk.py`、`app/data/adjuster.py`、`app/scoring/rubric.py`、`app/backtest/engine.py`、`api/routers/*` 時先跑一次測試再 commit，可以避免 regression。

> 前端的數字格式化 (`fmtPrice` / `fmtScore` / `fmtPct`) 邏輯搬到 `web/lib/format.ts`，由 TypeScript 端維護。

## .bat 工具盤點

公開（雙擊用）：

| 檔案 | 用途 |
|------|------|
| `launch.bat` | 啟動 FastAPI:8000 + Next.js:3000 + 開瀏覽器 |
| `stop.bat` | call `_kill-servers.bat` → 驗收兩個 port 都釋出 |
| `restart.bat` | call `_kill-servers.bat` → `snapshot_today()` 強制重產 signal_history → call `_launch-servers.bat` |
| `status.bat` | 顯示 port 8000 / 3000 LISTENING 狀態（含 PID）+ `signal_history.MAX(as_of)` vs `daily_price.MAX(date)` 對齊狀況 |
| `daily-update.bat` | 跑 `scripts.market_update --push`，給 Windows 排程跑（人也可手動雙擊） |
| `install-schedule.bat` | 註冊 Windows 工作排程，每日 15:30 自動跑 daily-update |
| `uninstall-schedule.bat` | 移除排程 |

**建議用法（改完策略/評分邏輯後）**

1. 雙擊 `status.bat` 確認 `signal_history.MAX(as_of)` 與 `daily_price.MAX(date)` 是否對齊
2. 雙擊 `restart.bat`（會先 stop，再 `snapshot_today()` 重產當日快照，最後 relaunch）
3. 再雙擊一次 `status.bat` 驗收服務狀態與快照版本

> `status.bat` / `restart.bat` 末尾會 `Press any key to continue`；設計上是給雙擊使用。若你在 shell 內跑，結束前要按任意鍵。

私有（被其他 .bat call，不要雙擊）：

| 檔案 | 用途 |
|------|------|
| `_launch-servers.bat` | `start "title" cmd /k uvicorn / next dev` 把兩個 server 開在獨立 cmd 視窗 |
| `_kill-servers.bat` | `netstat | findstr LISTENING | taskkill` + 收掉 `TW Stock *` 開頭的 cmd 視窗 |

**為什麼 restart.bat 要重產 snapshot？**

`ensure_fresh()`（[../app/scoring/snapshot_freshness.py](../app/scoring/snapshot_freshness.py)）只在 `signal_history.MAX(as_of) < daily_price.MAX(date)` 時才會重跑。改了 `app/scoring/engine.py` / `rubric.py` / `radar.py` 但沒新進資料的話，日期不會變，`ensure_fresh` 看不出 engine 換版 → 雷達/自選讀的還是舊邏輯算的快照、個股詳情頁是即時呼叫新 engine → 兩邊分數對不上。`restart.bat` 在重起前無條件 `snapshot_today()` 一次解決這個問題。

**找不到 process 在哪？** 雙擊 `stop.bat`。手動的話：
```
netstat -ano | findstr ":8000 :3000 "
taskkill /PID <pid> /T /F
```

## 前端 dev / build 工序（地雷）

**規則：在 `next dev` 還活著的時候不要跑 `npm run build`。**

- **Why**：兩者寫同一個 `web/.next/` 目錄。`next dev` 是逐頁增量 compile，`next build` 是一次寫完整 chunk graph。並行跑時 chunk hash / build manifest 會互相覆蓋 → dev server 直接 500、所有頁面噴 `Cannot find module './XXX.js'` / `vendor-chunks/clsx.js MODULE_NOT_FOUND`。一旦壞了，連停掉 build 也救不回來，必須整個收掉重來。
- **怎麼修壞掉的狀態**：
  1. 雙擊 `stop.bat`
  2. `rm -rf web/.next`
  3. 重跑 `launch.bat`
- **想做 production build 比對 bundle size**：
  - 先把 `next dev` 整個收掉
  - `cd web && npm run build`
  - 量完數字後想看 production 跑起來 → `npm start --port 3001`
  - 完事後再回 `launch.bat` 起 dev
- **想拍截圖驗收 mobile / 視覺**：用 dev server（`launch.bat` 那台 port 3000）就夠，**不要為了拍乾淨截圖而跑 build**。dev server 拍出來和 prod 視覺一致，差別只是 First Load JS 大小。

> 如果你（或 Claude）正在批次改 10+ 個檔案，dev 的 hot-reload 偶爾會吃不消（`Jest worker exceeded retry limit`），這時用上面的「修壞掉的狀態」三步驟還原即可。

## signal_history 回填（因子檢定）

當你改了 `app/scoring/*` 想重跑 `/diagnostics` 歷史 IC，使用：

```bash
# 先看要跑哪些日期
python -m scripts.backfill_signal_history --days 60 --dry-run

# 重算最近 60 個交易日（清舊算法）
python -m scripts.backfill_signal_history --days 60 --clear

# 補全歷史（只補缺日期）
python -m scripts.backfill_signal_history --days 1044 --skip-existing

# 先求快（不含 fundamentals），之後再補完整版
python -m scripts.backfill_signal_history --days 1044 --clear --no-fundamentals
```

效能參考（2026-04-29 實測）：
- 含 fundamentals：約 58 秒/天（約 16.8 小時 / 1044 天）
- `--no-fundamentals`：約 42 秒/天（約 12.2 小時 / 1044 天）
