# 資料來源與授權 (Data Sources & Licensing)

> **一句話總結**：本專案**程式碼**以 [MIT](LICENSE) 開源；**抓下來的市場資料**分屬各來源、
> 各有使用條款。**本 repo 不散布任何資料**（`data/` 全部 gitignore），資料由**使用者自行
> 抓取、自用**。此「只散布程式、不散布資料」的設計同時滿足所有來源中**最嚴格**的限制
> （Yahoo 禁止重散布資料）。
>
> 以下條款於 2026-06 由多來源主動查證（含官方 ToS / 授權原文）。條款可能變動，正式用途
> 請以各來源**官方頁面**為準。

## 總覽

| 來源 | 本專案用途 | 授權 / 條款 | 商業使用 | 可重散布資料？ | 標示 |
|------|-----------|------------|---------|--------------|------|
| **TWSE / TPEx OpenAPI** | 日 final OHLCV、指數、月營收、季財報 | 政府資料開放授權條款 v1.0（OGDL-Taiwan-1.0） | ✅ 允許 | ✅ 允許（須附標示） | **必須** |
| **FinMind**（免費版） | 財報 / 配息 / 分割（3 個免費 endpoint） | 套件 Apache-2.0；資料另依其 ToS | ⚠️ 不明確（README 寫非商業） | ❌ 未授權（保守視為不可） | 未強制（建議標示） |
| **yfinance / Yahoo Finance** | ETF 還原價、大盤指數歷史回補 | 套件 Apache-2.0；資料依 Yahoo ToS | ❌ 禁止 | ❌ **明文禁止** | — |
| **MOPS 公開資訊觀測站** | 歷史季財報 / 月營收回補 | 視取得路徑：OGDL（OpenAPI）或 TWSE 網站條款（直連網站） | 視路徑 | 視路徑（OpenAPI 可 / 網站直連不可） | **必須** |

---

## 1. 證交所 / 櫃買中心 OpenAPI（TWSE / TPEx）

- **用途**：每日盤後 final OHLCV、指數、月營收、季財報（綜損 + 資產負債），免 API key 的
  公開 OpenAPI 端點（`openapi.twse.com.tw`、`tpex.org.tw/openapi`）。
- **授權**：**政府資料開放授權條款－第 1 版**（Open Government Data License v1.0，SPDX:
  `OGDL-Taiwan-1.0`），登載於國家開放資料平台 data.gov.tw；提供機關為**金融監督管理
  委員會證券期貨局（證期局）/ 證交所 / 櫃買中心**。
- **商業使用**：✅ **允許**。OGDL 授權「不限目的」使用，明文含「開發各種產品或服務型態之
  衍生物」，與 CC BY 4.0 相容。
- **重散布**：✅ **允許**（OGDL 第 2 條允許再轉授權 / 散布）——但**重散布時必須一併攜帶
  OGDL 標示**（提供機關名稱 + 「依政府資料開放授權條款（第 1 版）」聲明 + 授權連結），
  否則「視為自始未取得授權」。
- **標示**：**必須**（見文末標示範例）。
- **注意**：① 約 2017-05 之前的歷史資料可能不在開放資料授權範圍內，重散布以 2017-05 之後
  的每日 feed 最穩妥。② OpenAPI 為 T+1 盤後 final（約 16:30 完整），非盤中。③ 官方未公布
  `openapi.twse.com.tw` 的明確流量上限（社群流傳的數字是針對舊版 `mis/www` 端點），請保守
  節流。
- 來源：data.gov.tw/license ・ spdx.org/licenses/OGDL-Taiwan-1.0.html ・
  openapi.twse.com.tw ・ tpex.org.tw/openapi

## 2. FinMind（免費版）

- **用途**：僅用**免費版**的三個 endpoint — `TaiwanStockFinancialStatements`、
  `TaiwanStockDividendResult`、`TaiwanStockSplitPrice`（財報 / 配息 / 分割）。
  > 註：「三個 endpoint」是**本專案的使用範圍**，非 FinMind 的官方分級；FinMind 免費版實際
  > 提供 50+ 資料集的限流存取。
- **授權（兩層）**：
  - **套件（程式碼）**：**Apache-2.0**（github.com/FinMind/FinMind 的 LICENSE 確認）。
  - **資料（服務）**：依 FinMind 的「使用條款與隱私權政策」。其 ToS 稱內容「供教育與參考
    用途」；GitHub README 更嚴格寫「教育、**非商業**用途」。
- **商業使用**：⚠️ **不明確** — 正式 ToS 對商業使用沉默、README 寫非商業。Apache-2.0 只
  涵蓋程式碼、不涵蓋資料。**保守起見：勿將免費版 FinMind 資料用於商業產品。**
- **重散布**：❌ **未授權**。ToS 與 README 均**無**任何允許第三方再散布 / 重新發布所抓資料
  的條款；無明文授權即**保守視為不可**重散布。
- **限流 / 注意**：未帶 token 約 **300 req/hr**；註冊並驗證信箱、帶 `token` 後約
  **600 req/hr**。短時間累積過多 4xx 會回 `ip banned`（403），約 30 分鐘自動解除。每週日
  00:00–03:00（台灣時間）維護停機。資料「僅供參考」，FinMind 對錯誤 / 延遲 / 中斷免責。
- 來源：github.com/FinMind/FinMind（LICENSE / README）・ finmind.github.io/PrivacyPolicy ・
  finmind.github.io/quickstart ・ pypi.org/project/finmind

## 3. yfinance / Yahoo Finance

- **用途**：僅用於補抓 **ETF 還原價**（0050 / 00631L / 00692 / 00878）與**大盤指數歷史**
  （`^TWII`），作為校正性回補，供個人研究使用。
- **授權（兩層）**：
  - **套件（程式碼）**：**Apache-2.0**（github.com/ranaroussi/yfinance）。
  - **資料**：依 **Yahoo Terms of Service**。yfinance 套件本身與 Yahoo **無隸屬關係**，
    其授權**不**賦予你任何對 Yahoo 資料的權利；其 README 明白要你回去看 Yahoo ToS。
- **商業使用**：❌ **禁止**（Yahoo ToS：未經書面同意不得為商業目的使用 Services / 內容 /
  API）。
- **重散布**：❌ **明文禁止**（兩個官方來源確認）：
  - Yahoo Help（SLN2310）：「You must not redistribute information displayed on or
    provided by Yahoo Finance.」
  - Yahoo ToS 2(h)/2.8：未經書面同意不得 reproduce / distribute / publicly perform /
    建立衍生資料庫等。
  - **把任何 Yahoo / yfinance 來源的資料（CSV、填好的 `stock.db`、fixtures、快取）commit
    進公開 repo＝重散布＝違反 Yahoo ToS。** 因此 `daily_price_adj` / 指數等 yfinance 衍生
    資料**一律不得進版控**。
- **注意**：Yahoo ToS 亦禁止未經授權的自動化抓取（2.4(ix)）；yfinance 正是這類工具，個人 /
  研究用途為業界**默許**而非**授權**，Yahoo 可隨時限流 / 變更非官方端點 / 封鎖，無 SLA。
- **本專案合規關鍵**：repo **不含任何資料**、使用者在自己機器上以 yfinance 即時抓取 → 此為
  唯一合規模式，請維持。
- 來源：legal.yahoo.com/.../otos ・ help.yahoo.com/kb/SLN2310 ・ github.com/ranaroussi/yfinance

## 4. MOPS 公開資訊觀測站

- **用途**：歷史季財報 / 月營收回補。本專案**最新**資料走 TWSE/TPEx OpenAPI（開放資料），
  **歷史** HTML 走 MOPS 舊版域 `mopsov.twse.com.tw`。
- **授權（依取得路徑而分）**：
  - 經 **OpenAPI / data.gov.tw** 取得 → **OGDL-Taiwan-1.0**（同第 1 節：商業 ✓、重散布 ✓、
    須標示）。
  - 直接抓 **MOPS / TWSE 網站** → 適用 **TWSE 網站使用條款 §8**（較嚴格）：未經書面同意
    不得逕自重製 / 散布 / 公開發表（已授權於政府開放資料平台者除外）。
- **重散布**：經 OpenAPI/開放資料平台者**可**（附 OGDL 標示）；僅存在於 MOPS 網站、未鏡像
  到開放資料者**不可**。要安全重散布，請以 **OpenAPI / data.gov.tw** 為取得路徑，並先確認
  該資料集已登載於 data.gov.tw。
- **注意**：MOPS POST 端點需帶 `Referer` header；過度輪詢會被 IP 限流，等幾分鐘再試。
  官方未公布明確流量上限。
- 來源：data.gov.tw/license ・ wwwc.twse.com.tw/zh/terms/use.html ・ mops.twse.com.tw

---

## 本專案的合規姿態（給 fork 的人）

1. **Repo 不散布資料**：`data/`（含 `*.db`）、`reports/`、`logs/`、`config.yaml`、
   `watchlist.yaml` 全部 `.gitignore`。任何人 fork 後**請勿** `git add -f` 把資料 / DB 推進
   git——尤其 yfinance / Yahoo 衍生資料，commit 進公開 repo 會違反 Yahoo ToS。
2. **資料使用者自抓**：依 [USAGE.md](USAGE.md) 用各腳本自行抓取到本機 `data/stock.db`。
3. **若你要重散布資料**（多數人不需要）：只有 **OGDL**（TWSE/TPEx OpenAPI、data.gov.tw、
   經 OpenAPI 取得的 MOPS）資料可重散布，且**必須附 OGDL 標示**；**切勿**重散布 Yahoo/
   yfinance 資料；FinMind 資料的重散布未獲授權。
4. **商業用途**：程式碼（MIT）可商業使用；但**資料**端 Yahoo 禁止商業、FinMind 不明確
   （README 非商業），請自行評估 / 改用有商業授權的資料源。

## OGDL 標示範例（重散布 TWSE/TPEx 開放資料時附上）

```
本資料／衍生物使用「政府資料開放授權條款（第 1 版）」之開放資料，
提供機關：金融監督管理委員會證券期貨局（臺灣證券交易所 / 證券櫃檯買賣中心），
授權條款：https://data.gov.tw/license
```
