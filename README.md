# Claude 用量 Discord 提醒

每天台灣時間 23:00，用 GitHub Actions 到 Discord 頻道 @ 你一次，告訴你 Claude 的 7 天用量還剩多少（也會附上 5 小時、Sonnet/Opus 的分項）。

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

## 重新登入

如果收到「換 access token 失敗」的訊息，代表 refresh token 失效了（例如 PAT 過期沒能寫回、或你在 claude.ai 登出所有裝置），重新跑一次第 5 步就好。

## 本機測試

```powershell
python bot.py --fake --dry-run
```

`--fake` 用假資料、`--dry-run` 只印出不發送。

## 小提醒

- GitHub 的排程尖峰時段可能延遲幾分鐘到半小時。
- 要改時間就改 `.github/workflows/daily.yml` 的 `cron`（UTC 時間，台灣要減 8 小時）。
