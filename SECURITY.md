# 安全政策 (Security Policy)

## 回報漏洞

如果你發現安全性問題（例如機密外洩、可被濫用的端點、相依套件漏洞），請**不要**開公開
issue，改用 GitHub 的私密回報管道：

- 進入本 repo 的 **Security** 分頁 → **Report a vulnerability**（GitHub Private
  Vulnerability Reporting），或
- 若該功能未啟用，請開一個標題不含敏感細節的 issue 請維護者私下聯繫。

請盡量附上重現步驟與影響範圍。這是一個個人自用專案，採盡力而為（best-effort）回應，
不提供 SLA。

## 機密管理現況

- 機密（FinMind token、Discord webhook、`ANTHROPIC_API_KEY`）一律走 `config.yaml`
  或環境變數，**皆已 `.gitignore`、從未進入 git**。範本見 `config.yaml.example`。
- 個人資料（持股 holdings、交易紀錄 trade_log、自選股 watchlist、跨 session 筆記）
  存在本機 `data/*.db` 與 gitignore 的檔案中，**不進 repo**。
- CI 內含 `gitleaks` 全歷史 secret scan（`fetch-depth: 0`），擋未來誤 commit；另有
  `pip-audit` + `npm audit` 的相依套件稽核（報告型）。

## 使用者自保提醒

- 自行 fork / clone 後，**push 前務必確認** `config.yaml`、`.env`、`data/` 未被
  `git add -f` 強加進來。
- 你的 FinMind token、Discord webhook 若不慎外洩，請到對應服務**重新產生 / 刪除舊的**。
