# Claude 用量 Discord 提醒

每天台灣時間 12:00 和 23:00，用 GitHub Actions 到 Discord 頻道 @ 你一次，告訴你 Claude 的 7 天用量還剩多少（也會附上 5 小時、Sonnet/Opus 的分項）。

- 跑在 GitHub 的伺服器上，電腦關機也會照常發
- 用 Discord Webhook 發訊息，不用架 bot 伺服器
- 抓不到資料時也會 @ 你，並說明原因

> 用量資料來自 Claude Code `/usage` 用的同一個介面（`api.anthropic.com/api/oauth/usage`）。這不是官方公開的 API，哪天改版可能就失效。

## 設定步驟

### 1. 建 Discord Webhook

Discord 頻道 → 編輯頻道 → 整合 → Webhook → 新 Webhook → 複製 Webhook 網址。

### 2. 取得你的 Discord 使用者 ID

Discord 使用者設定 → 進階 → 開啟「開發者模式」。然後在自己的頭像上按右鍵 → 複製使用者 ID。

### 3. 建 GitHub Personal Access Token（讓 bot 能更新 token）

Claude 的 refresh token 每用一次就會換新，bot 要把新的寫回 Secret，所以需要一把有權限的 token：

GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token
- Repository access：只選這個 repo
- Permissions → Repository permissions → **Secrets：Read and write**
- 有效期限自己選（過期後要換）

### 4. 設定 Secrets

在這個資料夾用 PowerShell 執行（每個指令會請你貼上值）：

```powershell
gh secret set DISCORD_WEBHOOK_URL --repo illillli/claude-usage-discord-bot
```

```powershell
gh secret set DISCORD_USER_ID --repo illillli/claude-usage-discord-bot
```

```powershell
gh secret set GH_PAT --repo illillli/claude-usage-discord-bot
```

### 5. 幫 bot 登入 Claude

```powershell
.\setup-token.ps1
```

它會用另一個獨立的設定資料夾開 Claude Code，你輸入 `/login` 登入、再 `/exit` 離開，腳本就會把 refresh token 直接存進 `CLAUDE_REFRESH_TOKEN`。你平常用的 Claude Code 不會被登出。

### 6. 測試

GitHub repo → Actions → Daily Claude usage report → Run workflow。Discord 應該馬上收到訊息。

## `/usage` 指令：隨時手動刷新

在 Discord 任何地方打 `/usage`，bot 就會馬上重跑一次報告：刪掉舊的那則、在頻道發新的，再私下回你一句「已更新」。新報告不會 @ 你，也不會影響 12:00、23:00 的排程。GitHub Actions 開機要時間，大約 20～40 秒後才會看到新報告。

運作方式：Discord → Cloudflare Worker（`worker/worker.js`）→ 觸發 `daily.yml`（force）→ `bot.py`。

### 1. 建 Discord Application

[Discord Developer Portal](https://discord.com/developers/applications) → New Application。
- **General Information**：記下 **Application ID** 和 **Public Key**。
- **Bot**：按 Reset Token，記下 **Bot Token**（只有註冊指令時用到，不用存進任何地方）。
- **Installation**：Installation Contexts 勾 **User Install**，Install Link 選 Discord Provided Link，Default Install Settings 的 User Install 加上 `applications.commands`。複製 Install Link 打開，把它裝到自己的帳號。

### 2. 建 GitHub token 給 Worker 用

GitHub → Fine-grained tokens → 只選這個 repo，權限只要 **Actions: Read and write**（跟 cron-job.org 用的那把一樣，可以共用）。

### 3. 部署 Cloudflare Worker

[Cloudflare](https://dash.cloudflare.com) 註冊（免費）→ Workers & Pages → Create → **Start with Hello World!** → 取個名字 → Deploy → Edit code，把 `worker/worker.js` 整個貼上取代 → Deploy。

到這個 Worker 的 Settings → Variables and Secrets 新增：

| 名稱 | 值 | 類型 |
|---|---|---|
| `DISCORD_PUBLIC_KEY` | Application 的 Public Key | Text |
| `DISCORD_APPLICATION_ID` | Application ID | Text |
| `GITHUB_REPO` | `illillli/claude-usage-discord-bot` | Text |
| `ALLOWED_USER_ID` | 你的 Discord 使用者 ID（只有你能用） | Text |
| `GITHUB_TOKEN` | 第 2 步的 token | **Secret** |

### 4. 把網址填回 Discord

複製 Worker 的網址（`https://xxx.workers.dev`），貼到 Developer Portal → General Information → **Interactions Endpoint URL** → Save。Discord 會先驗證一次，存得起來就代表 Worker 設定正確。

### 5. 註冊指令

```powershell
.\register-command.ps1
```

輸入 Application ID 和 Bot Token。完成後在 Discord 打 `/usage` 試試看（新指令有時要重開 Discord 才會出現）。

## 換算成 5 小時額度

比時間進度省 10% 以上時，報告會多一欄「🧮 換算成 5 小時額度」：剩下的週額度相當於幾個「5 小時用滿」，以及到重置前扣掉睡覺時間（00:00–08:00）最多還能用滿幾個。如果就算每個 5 小時都用滿還是用不完，自動報告會再多發一則訊息 @ 你一次（`/usage` 手動刷新不會）。

「5 小時用滿 ≈ 週額度幾 %」Anthropic 沒有公布，預設先用 15%。bot 每次執行都會把用量快照存進 `USAGE_SNAPSHOT`，只要兩次執行落在同一個 5 小時視窗、而且 5 小時用量增加了 10% 以上（例如中午報告後用了一陣子再打 `/usage`），就會用「週用量增加 ÷ 5 小時用量增加」校正比例，存進 `FIVE_HOUR_RATIO`。知道實際比例的話也可以直接設：`gh secret set FIVE_HOUR_RATIO --repo illillli/claude-usage-discord-bot`。

## 重新登入

如果收到「換 access token 失敗」的訊息，代表 refresh token 失效了（例如 PAT 過期沒能寫回、或你在 claude.ai 登出所有裝置），重新跑一次第 5 步就好。

## 本機測試

```powershell
python bot.py --fake --dry-run
```

`--fake` 用假資料、`--dry-run` 只印出不發送。

## 小提醒

- 要改發送時間：改 cron-job.org 上的時間，以及 `.github/workflows/backup.yml` 的備援 `cron`（UTC 時間，台灣要減 8 小時）。
- `bot.py` 只會在 11:00–15:00（中午）和 22:00–03:00（晚上）發送，改時間的話也要改 `slot_name()` 裡的時段。

## 排程怎麼運作

GitHub 的免費排程常常被延後好幾個小時，甚至直接跳過，所以：
- **主要**：[cron-job.org](https://cron-job.org) 每天 12:00、23:00 準時呼叫 GitHub 跑 `daily.yml`。
- **備援**：`backup.yml` 用 GitHub 自己的排程在 12:13、12:43、23:13、23:43、00:13 各試一次。
- `bot.py` 會自動擋掉同一個時段的重複發送，以及上面兩個時段以外的時間，所以每個時段只會收到一則。

`daily.yml` 刻意不放排程：public repo 60 天沒有 commit 時，GitHub 會停用含排程的 workflow，拆開後就算 `backup.yml` 被停用，cron-job.org 還是能正常觸發。

cron-job.org 的設定方式：

1. GitHub 建一把新的 fine-grained token：只選這個 repo，權限只要 **Actions: Read and write**。
2. cron-job.org 註冊後新增 Cronjob：
   - URL：`https://api.github.com/repos/illillli/claude-usage-discord-bot/actions/workflows/daily.yml/dispatches`
   - 時間：每天 23:00，時區選 Asia/Taipei（中午 12:00 再建一個一樣的排程）
   - Advanced → Request method 選 **POST**
   - Headers：`Authorization: Bearer <剛剛的 token>`、`Accept: application/vnd.github+json`
   - Request body：`{"ref":"main"}`
3. 存檔後按 Test run，GitHub Actions 應該會出現一次執行。

## 手動測試

Actions → Daily Claude usage report → Run workflow。不勾 **force** 時一樣會套用上面的時段和防重複規則；勾了就會直接發。
