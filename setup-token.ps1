# 幫 bot 另外登入一次 Claude，並把 refresh token 直接存進 GitHub Secret（不會顯示在畫面上）。
# 用法：在這個資料夾執行  .\setup-token.ps1
# 用獨立的設定資料夾登入，所以不會把你平常用的 Claude Code 登出。

$ErrorActionPreference = "Stop"
$repo = (gh repo view --json nameWithOwner -q .nameWithOwner)
$dir = Join-Path $PSScriptRoot ".claude-bot-login"
New-Item -ItemType Directory -Force $dir | Out-Null

Write-Host "接下來會打開 Claude Code。請輸入 /login 完成登入（選 Claude 訂閱帳號），登入成功後輸入 /exit 離開。" -ForegroundColor Cyan
$env:CLAUDE_CONFIG_DIR = $dir
claude
Remove-Item Env:CLAUDE_CONFIG_DIR

$cred = Join-Path $dir ".credentials.json"
if (-not (Test-Path $cred)) { throw "找不到 $cred，登入可能沒有完成。" }
$token = (Get-Content $cred -Raw | ConvertFrom-Json).claudeAiOauth.refreshToken
if (-not $token) { throw "credentials 裡沒有 refreshToken。" }

$token | gh secret set CLAUDE_REFRESH_TOKEN --repo $repo
Remove-Item -Recurse -Force $dir
Write-Host "完成：已寫入 $repo 的 CLAUDE_REFRESH_TOKEN，並刪除本機的暫存登入資料。" -ForegroundColor Green
