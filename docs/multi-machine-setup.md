# 兩台機器同步指南

> 主機（Primary）跑排程拉資料，副機（Secondary）只是來看儀表板的「讀取端」。
> 三層分開同步：**程式碼走 git、DB 走 OneDrive、設定（含 token）手動帶**。

---

## 架構速覽

```
                 GitHub (private repo)              ← 程式碼（雙向 push/pull）
maplestartend/twstock-research
   ▲                ▲
   │ git push       │ git pull
   │                │
┌──┴────┐       ┌───┴──────┐
│ 主機   │       │ 副機      │
│ Desktop│       │ Desktop   │
│ Win 11 │       │ Win 11    │
└──┬─────┘       └───┬───────┘
   │ 排程跑 daily-     │ 不跑排程
   │ update + 自動備份 │ 用前先 sync-from-cloud.bat
   ▼                  ▲ 拉最新 DB
┌──────────────────────┴────┐
│ OneDrive\台股備份\         │ ← stock.db 走這層
│  stock_20260426.db         │
│  stock_20260425.db         │
│  ...（保留 14 日 + 8 週 + 12 月）
└────────────────────────────┘

config.yaml（含 FinMind token / Discord webhook）→ 不進 git，手動帶
```

---

## 主機端（已設定完成 ✅）

> 你目前正在用的這台 Desktop。已經做完的事：

- [x] git init 並 push 到 `maplestartend/twstock-research`（private）
- [x] `config.yaml` 啟用 `backup.enabled: true`，path 指向 `C:/Users/User/OneDrive/台股備份`
- [x] OneDrive for Desktop 已裝（路徑 `C:\Users\User\OneDrive`）

主機需要繼續做的事：

- [ ] **執行排程**：`install-schedule.bat`（每日 15:30 自動 daily-update + 備份）
- [ ] 第一次 `python -m scripts.market_update` 跑完後，去 `C:\Users\User\OneDrive\台股備份` 確認有 `stock_YYYYMMDD.db` 出現
- [ ] watchlist / config 改完記得 `git commit && git push`

主機**不要**跑 `sync-from-cloud.bat` — 那是副機用的。

---

## 副機端（待設定）

### 一次性環境準備

1. **裝 Python 3.11+**（[python.org](https://www.python.org/downloads/)）— 安裝時勾「Add to PATH」
2. **裝 Node.js 20+**（[nodejs.org](https://nodejs.org/)）
3. **裝 Git**（[git-scm.com](https://git-scm.com/)）
4. **裝 GitHub CLI**：PowerShell 跑 `winget install --id GitHub.cli -e`
5. **登入 OneDrive**：用同一個 Microsoft 帳號登入 OneDrive Desktop。
   - 個人版同步資料夾：`%USERPROFILE%\OneDrive\`
   - 公司版（Business）：`%USERPROFILE%\OneDrive - <組織名>\`（路徑不同！見下方注意）

### Clone + 初始化

開 PowerShell（不是 cmd），到想放專案的位置（範例放在 `D:\stock\`）：

```powershell
cd D:\stock
gh auth login                       # 瀏覽器登入 maplestartend 帳號
gh repo clone maplestartend/twstock-research stock
cd stock

# Python venv
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.lock.txt

# 前端
cd web
npm install
cd ..
```

### 帶 config.yaml 過去（含 token，不能進 git）

兩種方式擇一：

**方式 A — 直接複製檔案**（最快）
1. 主機把 `config.yaml` 用隨身碟 / Signal / 1Password Secure Note 帶到副機
2. 放到副機的 `<repo>\config.yaml`

**方式 B — 用環境變數**（安全度高，token 不落地）
1. 副機保留 git 上 commit 的 `config.yaml.example`，重新命名 / 複製為 `config.yaml`
2. 系統環境變數設兩個：
   ```
   FINMIND_TOKEN=eyJ0eXAi...（從主機 config.yaml 複製）
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ```
   PowerShell 永久設定：
   ```powershell
   [Environment]::SetEnvironmentVariable('FINMIND_TOKEN', '貼 token', 'User')
   [Environment]::SetEnvironmentVariable('DISCORD_WEBHOOK_URL', '貼 webhook', 'User')
   ```

> `app/config.py` 會優先讀 env var，不存在才退回 yaml。

### 確認 OneDrive 路徑（重要！副機用 Business 版時必看）

副機如果用的是公司 / 學校的 OneDrive Business，同步資料夾名稱會變成
`%USERPROFILE%\OneDrive - <組織名>\` 而不是純 `%USERPROFILE%\OneDrive\`。

副機的 `sync-from-cloud.bat` 開頭有一行：
```bat
set "CLOUD_DIR=%USERPROFILE%\OneDrive\台股備份"
```
如果是 Business 版要改成（例如組織叫 ACME）：
```bat
set "CLOUD_DIR=%USERPROFILE%\OneDrive - ACME\台股備份"
```

驗證路徑對不對：開檔案總管，導到該資料夾，能看到 `stock_YYYYMMDD.db` 就 OK。

### 拉最新 DB + 啟動

雙擊 `sync-from-cloud.bat` → 自動找雲端最新一份覆蓋 `data\stock.db` → 雙擊 `launch.bat`。

第一次建議跑一下 `python -m pytest tests/ -q` 驗證 DB 沒被同步壞掉（應該 323 passed + 1 xfailed）。

> 命名澄清：`sync-from-cloud.bat` 名字像「雙向同步」其實是「**單向 pull**」（把 OneDrive
> 的最新 DB 蓋到本機 `data\stock.db`）。新增 `pull-latest-db.bat` 為更精確的別名，
> 兩者效果完全相同；舊雙擊習慣不受影響。

### 副機**不要**做的事

- ❌ 不要跑 `install-schedule.bat`（避免兩台同時打 TWSE / FinMind）
- ❌ 不要在副機跑 `python -m scripts.market_update`（同上）
- ❌ 不要在副機改完 watchlist 之後忘了 push（會被主機下次 push 蓋掉）

---

## 日常工作流

### 主機（每天）

什麼都不用做，排程自動跑。要看儀表板就 `launch.bat`。

改了 watchlist / 程式碼：
```bash
git add -A
git commit -m "..."
git push
```

### 副機（要用的時候）

```
1. git pull               ← 拉最新程式碼
2. sync-from-cloud.bat    ← 拉最新 DB
3. launch.bat             ← 開儀表板
```

改了東西要 push（雖然副機不太該改東西，但若臨時改了 watchlist）：
```bash
git add -A && git commit -m "..." && git push
```
回到主機要記得先 `git pull`。

---

## 常見問題

### Q: 主機 push、副機 pull 後，沒看到新資料？
A: `git pull` 只同步**程式碼**，DB 是分開走 OneDrive。副機要再跑 `sync-from-cloud.bat`。

### Q: OneDrive 還沒同步好就跑 sync-from-cloud.bat？
A: 雙擊 .bat 之前確認 OneDrive 工具列 icon 是綠勾（不是同步中的圈圈）。OneDrive 同步 600MB 通常 1-3 分鐘。

### Q: 副機跑 launch 後，分數跟主機不一致？
A: 99% 是 DB 沒同步到最新。重跑 `sync-from-cloud.bat`。如果還是不一致，主機 `git push`、副機 `git pull` 確保 scoring 程式碼也對齊。

### Q: 主機 OneDrive 容量爆了？
A: `config.yaml` 的 `keep_days: 14, keep_weeks: 8, keep_months: 12` 會自動輪刪。每份 DB ~600MB × 14 日 + 8 週 + 12 月 ≈ 20GB。OneDrive 免費 5GB 不夠，要 365 訂閱（1TB）才夠。撐不住可降到 `keep_days: 7, keep_weeks: 4, keep_months: 0`，~6GB。

### Q: 副機要不要也排程備份？
A: 不要。只有主機跑 daily-update + 備份；副機只是讀取端。
