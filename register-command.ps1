# 向 Discord 註冊 /usage 斜線指令（只要跑一次；改指令說明時再跑）。
# 用法：在這個資料夾執行  .\register-command.ps1
# 需要 Discord Developer Portal 裡的 Application ID 和 Bot Token（Bot 頁 → Reset Token）。

$ErrorActionPreference = "Stop"
$appId = Read-Host "Application ID"
$secure = Read-Host "Bot Token（輸入時不會顯示）" -AsSecureString
$botToken = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

# integration_types 0=裝在伺服器、1=裝在使用者；contexts 0=伺服器、1=和 bot 的私訊、2=其他私訊
$commands = @(
    @{
        name              = "usage"
        description       = "立刻更新一次 Claude 用量報告"
        type              = 1
        integration_types = @(0, 1)
        contexts          = @(0, 1, 2)
    }
)
$body = ConvertTo-Json -InputObject $commands -Depth 5

$res = Invoke-RestMethod -Method Put `
    -Uri "https://discord.com/api/v10/applications/$appId/commands" `
    -Headers @{ Authorization = "Bot $botToken" } `
    -UserAgent "DiscordBot (https://github.com/illillli/claude-usage-discord-bot, 1.0)" `
    -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($body))

Write-Host "完成：已註冊 /$($res[0].name)。" -ForegroundColor Green
