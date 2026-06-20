# 台股研究儀表板 · TW Stock Research Dashboard

> 一套**本地、自架、免費**的台灣股市研究全端工具：個股多因子評分、雷達選股、策略 / 投組 /
> 除權息回測、持股組合管理、自選股追蹤，外加可選的 AI 敘事解讀。
>
> A self-hosted, local-first full-stack toolkit for researching the Taiwan stock
> market — multi-factor stock scoring, screening radar, strategy/portfolio/dividend
> backtesting, holdings management, and a watchlist tracker, with optional LLM narratives.

[![CI](https://github.com/maplestartend/twstock-research/actions/workflows/ci.yml/badge.svg)](https://github.com/maplestartend/twstock-research/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.12-3776ab)
![Node](https://img.shields.io/badge/Node-22-339933)
![Next.js](https://img.shields.io/badge/Next.js-15-000000)

> ⚠️ **非投資建議**。所有輸出僅供個人研究與教育，不構成投資建議，不保證資料正確性與即時性，
> 依此操作風險自負。詳見 [DISCLAIMER.md](DISCLAIMER.md)。

---

## 這是什麼

每天盤後抓全市場資料，算出每檔股票的**短 / 中 / 長 / 綜合**四維分數（多因子、經
forward-return IC 驗證、regime-aware 權重），並以網頁儀表板呈現：戰情室、族群熱力圖、
雷達選股、個股決策工作台、持股組合、回測工具室等。資料留在本機 SQLite，無雲端依賴、
無訂閱費，只需一個免費的 FinMind token。

- **資料**：證交所 / 櫃買中心 OpenAPI（日 final OHLCV、指數、月營收、季財報）+ FinMind
  免費版（財報 / 配息 / 分割）+ yfinance（ETF 還原價、大盤指數歷史）+ MOPS（歷史財報）。
- **特色**：盤中即時報價會即時重算短 / 中分數（不污染回測快照）；長期分數吃 ROE / EPS /
  股利等財報指標、盤中不動。

## ✨ 功能總覽

| 頁面 | 重點 |
|------|------|
| 🏠 **今日戰情室** | 大盤體質燈號、持股 KPI（盤中即時市值 / 損益）、持股明細、自選股 Top/Bottom、雷達命中 Top 10、5 日除權息、資料新鮮度 |
| 📈 **族群輪動** | 44 產業 treemap 熱力圖（磚面積 = 成交值占比、顏色 = 加權漲跌），點磚 drill-down 進該產業 |
| 📊 **自選股總覽** | 個股 / ETF 分流、短中長綜合分、標籤分組、收盤 / 即時切換 |
| 🔍 **個股詳情** | K 線（可還原價）、評分拆解、**收盤 / 即時 / 假設**三模式、同業比較、月營收 YoY、分數走勢、ATR 動態出場 |
| 💼 **我的持股** | KPI、持股明細（盤中即時）、集中度風險提醒、FIFO 已實現損益、交易紀錄、ATR 停損 + Chandelier 停利、匯出 Excel |
| 🎯 **雷達掃描** | 8 內建策略（短線強勢 / 中期波段 / 長期價值 / 回檔布局 / 三榜俱佳 / 營收成長 / 營收加速 / 量能動能）、收盤 / 即時、批次加自選、匯出 CSV/Excel |
| 📅 **除權息行事曆** | 未來 7~90 天除權息，含殖利率估算 |
| 📜 **歷史追蹤** | 回看某天雷達選股到今天的表現 |
| 🔬 **回測工具室** | 策略回測、投組回測、參數掃描（含 walk-forward 防過擬合）、除權息回測、權重調優 |
| 🩺 **資料品質 / 📊 因子檢定** | 異常掃描；對歷史快照算 forward-return IC（Newey-West HAC CI） |

> AI 敘事（選用）：設 `ANTHROPIC_API_KEY` 後，個股詳情頁可一鍵產生白話解讀；未設則優雅降級。

## 🧱 技術棧

| 層 | 技術 |
|----|------|
| 後端 | **FastAPI** + **Python 3.12**，薄殼包裝 `app.*`（scoring / backtest / portfolio / data）成 REST |
| 資料庫 | **SQLite**（WAL 模式），全部本機，無外部 DB |
| 前端 | **Next.js 15** App Router + **React 19** + **TypeScript 5.7** + **Tailwind v4**（自家 design tokens） |
| 圖表 | **d3-hierarchy**（產業熱力圖 treemap）+ **Recharts**（線 / 面 / scatter）+ **lightweight-charts**（K 線） |
| 選用 | **Anthropic**（LLM 敘事）、**Discord webhook**（推播） |

## 🚀 快速開始

> Windows 使用者有一鍵 `.bat`；其他 OS 直接跑 Python / Node 指令（`.bat` 只是啟動器）。

```bash
# 1. 後端相依
pip install -r requirements.lock.txt

# 2. 設定（複製範本後填入 FinMind token；config.yaml 已 gitignore）
cp config.yaml.example config.yaml
#    申請免費 token：https://finmindtrade.com/   也可改用環境變數 FINMIND_TOKEN

# 3. 首次歷史回補（一次性，~30 分鐘）
python -m scripts.market_update --days 260
python -m scripts.update_adj           # 自選股 / ETF 還原價
python -m scripts.refresh_industry     # 產業別

# 4a. 啟動（Windows 推薦）：雙擊 stock.bat → 選單操作；或 launch.bat
# 4b. 啟動（任何 OS）：
.venv/Scripts/python -m uvicorn api.main:app --reload --port 8000   # 後端
cd web && npm install && npm run dev                               # 前端
```

開瀏覽器 `http://localhost:3000`。每日盤後跑 `python -m scripts.market_update`（或排程
`daily-update.bat`）更新資料 + 拍訊號快照。

完整安裝、各頁用法、指令列腳本、疑難排解 → **[USAGE.md](USAGE.md)**。

## 📁 專案結構

```
app/        評分 / 回測 / 投組 / 風險 / 資料抓取等核心邏輯（純 Python，可單獨測試）
api/        FastAPI 薄殼：schemas + routers，把 app.* 包成 REST
web/        Next.js 前端（App Router）
scripts/    指令列工具（market_update、backfill、prune、dump_openapi…）
docs/       架構 / API / 前端 / 維運規格
*.bat       Windows 一鍵啟動 / 更新 / 排程腳本
```

## 📊 資料來源

程式碼開源（MIT），但**抓下來的市場資料分屬各來源、有各自的使用條款**，本 repo
**不散布任何資料檔**（`data/` 全部 gitignore），資料需使用者自行抓取自用。各來源授權
與重點（含 yfinance / Yahoo「不可重散布資料」的法律眉角）詳見
**[DATA_SOURCES.md](DATA_SOURCES.md)**。

## 📚 文件

- [USAGE.md](USAGE.md) — 安裝 + 各頁用法 + 指令列腳本
- [docs/architecture.md](docs/architecture.md) — 架構、資料庫結構、評分權重與 IC 量測
- [docs/api-spec.md](docs/api-spec.md) — REST API 規格
- [docs/frontend-spec.md](docs/frontend-spec.md) — 前端規格
- [docs/operations.md](docs/operations.md) — 維運（推播 / 備份 / 排程 / CI）
- [CONTRIBUTING.md](CONTRIBUTING.md) — 開發 / 測試 / 提交慣例
- [DISCLAIMER.md](DISCLAIMER.md) — 免責聲明 ・ [SECURITY.md](SECURITY.md) — 安全回報

## 📄 授權

程式碼以 **[MIT](LICENSE)** 授權釋出。**授權僅涵蓋程式碼，不涵蓋市場資料**——資料屬各
來源、使用者自抓自用，詳見 [DATA_SOURCES.md](DATA_SOURCES.md)。
