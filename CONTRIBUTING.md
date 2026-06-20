# 貢獻指南 (Contributing)

這是一個個人自用專案，但歡迎 issue 與 PR。以下是讓改動順利合併的最低門檻。

## 環境

- **後端**：Python 3.12（見 [.python-version](.python-version)）。
  ```bash
  pip install -r requirements.lock.txt    # 重現安裝走 lock 檔
  ```
- **前端**：Node 22（見 [web/.nvmrc](web/.nvmrc)）。
  ```bash
  cd web && npm ci
  ```
- 複製 `config.yaml.example` 成 `config.yaml`，填入 FinMind token（或設 `FINMIND_TOKEN`
  環境變數）。完整安裝流程見 [USAGE.md](USAGE.md)。

## 提交前自我檢查（對齊 CI 硬關卡）

CI（[.github/workflows/ci.yml](.github/workflows/ci.yml)）會在 push / PR 時擋這些，請先在本機跑過：

```bash
# 後端：DB-independent 測試子集 + OpenAPI 契約 drift
python -m pytest -m "not needs_prod_db" -q
python -m scripts.dump_openapi --check

# 前端：型別檢查
cd web && npx tsc --noEmit

# Windows .bat lint（BOM / CR-only 行尾 = fail）
python scripts/check_bat.py *.bat
```

> 本機若有 `data/stock.db`（~3GB，不在 git），可跑**全套** `python -m pytest -q`，
> 涵蓋標了 `needs_prod_db` 的整合測試；CI 因無 prod DB 而排除它們。

另有 `gitleaks`（secret scan）、`pip-audit` + `npm audit`（SCA，報告型）也在 CI 跑。

## 專案慣例（重要地雷）

完整工作守則見 [CLAUDE.md](CLAUDE.md)，幾條最常踩的：

1. **不要在 `next dev` 跑著時跑 `npm run build`** — 兩者共用 `web/.next/`，會互相覆蓋。
2. **Treemap 用 d3-hierarchy，不要用 Recharts**（後者 squarify 在極端比例資料上崩版）。
3. **改 `web/components|app|lib|styles/` 後必跑 Playwright 截圖驗收** — type-check 綠燈
   不代表畫面對。腳本見 [web/scripts/](web/scripts/)。
4. **改 `app/scoring/*` / `app/backtest/*` / `app/risk.py` / `app/portfolio.py` 後跑
   `restart.bat`** — 強制重算 `signal_history` 快照，否則列表頁讀舊快照、詳情頁即時算
   新引擎，兩邊分數會分歧。（純效能、輸出 bit-identical 的改動除外。）
5. **改 endpoint 要同步更新** [docs/api-spec.md](docs/api-spec.md) 並重生
   `python -m scripts.dump_openapi`。
6. **改功能要同步更新** [USAGE.md](USAGE.md) / requirements / 相關 `.bat`。
7. **`.bat` 一律 CRLF 行尾、無 BOM、輸出非 ASCII 要 `chcp 65001`**（見
   `scripts/check_bat.py` 與 windows-bat-guardrails）。

## Commit / PR

- 一人開發慣例：小改動直接 commit 到 `main`；較大或有風險的改動開分支發 PR。
- Commit message 用 conventional-style 前綴（`feat:` / `fix:` / `perf:` / `docs:` / `chore:`）。
- PR 請說明動機、測試方式、是否需要 `restart.bat`。
