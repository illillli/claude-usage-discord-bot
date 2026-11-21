"""每天把 Claude 7 天用量剩餘多少，@ 你一次發到 Discord。

只用 Python 標準函式庫，不需要 pip install。

環境變數：
  CLAUDE_REFRESH_TOKEN   Claude OAuth refresh token（建議；會自動換新並寫回 GitHub Secret）
  CLAUDE_ACCESS_TOKEN    或直接給 access token（本機測試用，幾小時就過期）
  DISCORD_WEBHOOK_URL    Discord 頻道的 Webhook 網址
  DISCORD_USER_ID        要 @ 的人的 Discord 使用者 ID（一串數字）
  TIMEZONE               顯示時間用的時區，預設 Asia/Taipei
  GH_TOKEN + GITHUB_REPOSITORY
                         有設的話，refresh token 輪替後會用 gh CLI 寫回 Secret

參數：
  --dry-run              只印出訊息，不發到 Discord
  --fake                 不打 Claude API，用假資料（測試排版用）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code 的 OAuth client id
TOKEN_URLS = [
    "https://platform.claude.com/v1/oauth/token",
    "https://console.anthropic.com/v1/oauth/token",
]
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-usage-discord-bot/1.0"


def http_json(url: str, *, data: dict | None = None, headers: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    if body:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"{url} 回傳 HTTP {e.code}: {detail}") from None


def mask(value: str) -> None:
    """在 GitHub Actions log 裡把敏感值遮掉。"""
    if os.environ.get("GITHUB_ACTIONS") == "true" and value:
        print(f"::add-mask::{value}")


def save_rotated_refresh_token(new_token: str) -> None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (repo and os.environ.get("GH_TOKEN") and shutil.which("gh")):
        print("⚠️ refresh token 已輪替，但沒有 GH_TOKEN，無法寫回 Secret。下次執行可能會失敗。")
        return
    subprocess.run(
        ["gh", "secret", "set", "CLAUDE_REFRESH_TOKEN", "--repo", repo],
        input=new_token.encode(),
        check=True,
    )
    print("已把新的 refresh token 寫回 GitHub Secret。")


def get_access_token() -> str:
    refresh = os.environ.get("CLAUDE_REFRESH_TOKEN", "").strip()
    if not refresh:
        access = os.environ.get("CLAUDE_ACCESS_TOKEN", "").strip()
        if not access:
            raise RuntimeError("沒有設定 CLAUDE_REFRESH_TOKEN 或 CLAUDE_ACCESS_TOKEN")
        return access

    last_err: Exception | None = None
    for url in TOKEN_URLS:
        try:
            res = http_json(url, data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": CLIENT_ID,
            })
            break
        except RuntimeError as e:
            last_err = e
    else:
        raise RuntimeError(f"換 access token 失敗（refresh token 可能失效，需要重新登入）：{last_err}")

    access = res["access_token"]
    mask(access)
    new_refresh = res.get("refresh_token")
    if new_refresh and new_refresh != refresh:
        mask(new_refresh)
        # refresh token 是一次性的，一定要馬上存起來
        save_rotated_refresh_token(new_refresh)
    return access


def get_usage(access_token: str) -> dict:
    return http_json(USAGE_URL, headers={
        "Authorization": f"Bearer {access_token}",
        "anthropic-beta": "oauth-2025-04-20",
    })


def fmt_reset(iso: str | None, tz: ZoneInfo) -> str:
    if not iso:
        return "—"
    dt = datetime.fromisoformat(iso).astimezone(tz)
    now = datetime.now(tz)
    hours = max(0, (dt - now).total_seconds() / 3600)
    left = f"{hours / 24:.1f} 天後" if hours >= 24 else f"{hours:.1f} 小時後"
    weekday = "一二三四五六日"[dt.weekday()]
    return f"{dt:%m/%d}（{weekday}）{dt:%H:%M}，{left}"


def bar(remaining: float, width: int = 20) -> str:
    filled = round(remaining / 100 * width)
    return "█" * filled + "░" * (width - filled)


def build_message(usage: dict, user_id: str, tz: ZoneInfo) -> dict:
    week = usage.get("seven_day") or {}
    used = float(week.get("utilization") or 0)
    remaining = max(0.0, 100 - used)
    color = 0x2ECC71 if remaining >= 50 else 0xF1C40F if remaining >= 20 else 0xE74C3C

    fields = [{
        "name": "7 天總用量",
        "value": f"`{bar(remaining)}`\n剩 **{remaining:.0f}%**（已用 {used:.0f}%）\n重置：{fmt_reset(week.get('resets_at'), tz)}",
        "inline": False,
    }]
    for key, label in [("seven_day_opus", "7 天 Opus"), ("seven_day_sonnet", "7 天 Sonnet")]:
        w = usage.get(key)
        if w and w.get("utilization") is not None:
            u = float(w["utilization"])
            fields.append({"name": label, "value": f"剩 {100 - u:.0f}%", "inline": True})
    five = usage.get("five_hour")
    if five and five.get("utilization") is not None:
        fields.append({"name": "5 小時", "value": f"剩 {100 - float(five['utilization']):.0f}%", "inline": True})

    mention = f"<@{user_id}> " if user_id else ""
    return {
        "content": f"{mention}晚安，今天的 Claude 7 天用量還剩 **{remaining:.0f}%**",
        "allowed_mentions": {"users": [user_id] if user_id else []},
        "embeds": [{
            "title": "Claude 用量報告",
            "color": color,
            "fields": fields,
            "footer": {"text": datetime.now(tz).strftime("%Y-%m-%d %H:%M")},
        }],
    }


def build_error_message(err: Exception, user_id: str) -> dict:
    mention = f"<@{user_id}> " if user_id else ""
    return {
        "content": f"{mention}⚠️ 今天抓不到 Claude 用量：\n```{str(err)[:1500]}```\n"
                   "如果是 token 失效，照 README 的「重新登入」步驟更新 `CLAUDE_REFRESH_TOKEN`。",
        "allowed_mentions": {"users": [user_id] if user_id else []},
    }


def send_discord(payload: dict) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise RuntimeError("沒有設定 DISCORD_WEBHOOK_URL")
    http_json(url, data=payload)


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    fake = "--fake" in sys.argv
    tz = ZoneInfo(os.environ.get("TIMEZONE") or "Asia/Taipei")
    user_id = os.environ.get("DISCORD_USER_ID", "").strip()

    try:
        if fake:
            usage = {
                "five_hour": {"utilization": 12.0, "resets_at": "2099-01-01T00:00:00+00:00"},
                "seven_day": {"utilization": 63.0, "resets_at": datetime.now(tz).replace(microsecond=0).isoformat()},
                "seven_day_opus": None,
                "seven_day_sonnet": {"utilization": 8.0, "resets_at": None},
            }
        else:
            usage = get_usage(get_access_token())
        payload = build_message(usage, user_id, tz)
        ok = True
    except Exception as e:  # noqa: BLE001 - 失敗也要通知
        payload = build_error_message(e, user_id)
        ok = False
        print(f"錯誤：{e}", file=sys.stderr)

    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        send_discord(payload)
        print("已發送到 Discord。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
