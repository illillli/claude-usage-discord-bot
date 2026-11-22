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
from datetime import datetime, timedelta
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


def save_secret(name: str, value: str) -> bool:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (repo and os.environ.get("GH_TOKEN") and shutil.which("gh")):
        return False
    subprocess.run(["gh", "secret", "set", name, "--repo", repo], input=value.encode(), check=True)
    return True


def save_rotated_refresh_token(new_token: str) -> None:
    if save_secret("CLAUDE_REFRESH_TOKEN", new_token):
        print("已把新的 refresh token 寫回 GitHub Secret。")
    else:
        print("⚠️ refresh token 已輪替，但沒有 GH_TOKEN，無法寫回 Secret。下次執行可能會失敗。")


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


WEEK_SECONDS = 7 * 24 * 3600
BLOCKS = 10


def to_dt(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None


def hours_until(dt: datetime | None) -> float:
    if not dt:
        return 0.0
    return max(0.0, (dt - datetime.now(ZoneInfo("UTC"))).total_seconds() / 3600)


def time_elapsed_pct(resets_at: str | None) -> float | None:
    """7 天週期已經過了幾 %（以下次重置時間往回推 7 天當起點）。"""
    dt = to_dt(resets_at)
    if not dt:
        return None
    left = hours_until(dt) * 3600
    return min(100.0, max(0.0, (WEEK_SECONDS - left) / WEEK_SECONDS * 100))


def emoji_bar(pct: float, full: str) -> str:
    filled = max(0, min(BLOCKS, round(pct / 100 * BLOCKS)))
    return full * filled + "⬛" * (BLOCKS - filled)


def level_square(remaining: float) -> str:
    return "🟩" if remaining >= 50 else "🟨" if remaining >= 20 else "🟥"


def discord_time(dt: datetime | None) -> str:
    """Discord 時間戳記：會自動用看的人的時區顯示，還有「幾小時後」的倒數。"""
    if not dt:
        return "—"
    ts = int(dt.timestamp())
    return f"<t:{ts}:f>（<t:{ts}:R>）"


def fmt_left(hours: float) -> str:
    if hours >= 24:
        return f"{hours / 24:.1f} 天"
    if hours >= 1:
        return f"{hours:.1f} 小時"
    return f"{hours * 60:.0f} 分鐘"


def weekday_now(tz: ZoneInfo) -> int:
    """0=週一 … 6=週日。測試時可用 FAKE_WEEKDAY 覆寫。"""
    fake = os.environ.get("FAKE_WEEKDAY")
    return int(fake) if fake else datetime.now(tz).weekday()


def weekend_message(used: float, elapsed: float, remaining: float, resets_at: str | None) -> str:
    spare = elapsed - used  # 比時間進度少用了幾個百分點
    days_left = hours_until(to_dt(resets_at)) / 24
    msg = f"你比時間進度少用了約 **{spare:.0f}%**，可以放心多做一點事！"
    if days_left >= 1:
        msg += f"\n距離重置還有 {days_left:.1f} 天，平均每天可以用 **{remaining / days_left:.0f}%**。"
    else:
        msg += "\n不到一天就重置了，剩下的額度不用就浪費囉！"
    return msg


def build_message(usage: dict, user_id: str, tz: ZoneInfo, pat_days_left: float | None = None) -> dict:
    week = usage.get("seven_day") or {}
    used = float(week.get("utilization") or 0)
    remaining = max(0.0, 100 - used)
    reset_dt = to_dt(week.get("resets_at"))
    color = 0x2ECC71 if remaining >= 50 else 0xF1C40F if remaining >= 20 else 0xE74C3C

    elapsed = time_elapsed_pct(week.get("resets_at"))
    lines = [
        "**📅 7 天總用量**",
        f"{emoji_bar(remaining, level_square(remaining))}  **{remaining:.0f}%** 剩餘",
        f"-# 已用 {used:.0f}%",
    ]
    if elapsed is not None:
        if used > elapsed + 5:
            pace, pace_icon = "用得比時間快，省著點", "🔴"
        elif used < elapsed - 5:
            pace, pace_icon = "還有餘裕", "🟢"
        else:
            pace, pace_icon = "跟時間差不多", "🟡"
        lines += [
            "",
            "**⏳ 時間進度**",
            f"{emoji_bar(elapsed, '🟦')}  **{elapsed:.0f}%** 已過",
            f"-# 離重置還有 {fmt_left(hours_until(reset_dt))}",
            "",
            f"**🧭 使用節奏**　{pace_icon} {pace}",
        ]
    else:
        pace = ""

    fields = []
    five = usage.get("five_hour")
    if five and five.get("utilization") is not None:
        fields.append({"name": "⚡ 5 小時", "value": f"剩 **{100 - float(five['utilization']):.0f}%**", "inline": True})
    for key, label in [("seven_day_sonnet", "🎼 Sonnet 週額度"), ("seven_day_opus", "💎 Opus 週額度")]:
        w = usage.get(key)
        if w and w.get("utilization") is not None:
            fields.append({"name": label, "value": f"剩 **{100 - float(w['utilization']):.0f}%**", "inline": True})
    fields.append({"name": "🔄 下次重置", "value": discord_time(reset_dt), "inline": False})

    if pace == "還有餘裕" and weekday_now(tz) in (4, 5):  # 週五、週六
        fields.append({"name": "🎉 週末加碼",
                       "value": weekend_message(used, elapsed, remaining, week.get("resets_at")),
                       "inline": False})
    warn = pat_warning(pat_days_left)
    if warn:
        fields.append({"name": "🔑 GH_PAT 快過期了", "value": warn, "inline": False})

    mention = f"<@{user_id}> " if user_id else ""
    return {
        "content": f"{mention}🌙 晚安！今天的 Claude 用量報告來了",
        "allowed_mentions": {"users": [user_id] if user_id else []},
        "embeds": [{
            "author": {"name": "Claude 用量小幫手"},
            "title": f"本週還剩 {remaining:.0f}%",
            "description": "\n".join(lines),
            "color": color,
            "fields": fields,
            "footer": {"text": "每天 23:00 自動回報"},
            "timestamp": datetime.now(ZoneInfo("UTC")).isoformat(),
        }],
    }


PAT_WARN_DAYS = 7


def get_pat_days_left() -> float | None:
    """問 GitHub API，從回應標頭讀出 GH_PAT 的到期時間，回傳剩幾天（沒設到期日就回 None）。"""
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        return None
    req = urllib.request.Request("https://api.github.com/rate_limit", headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.headers.get("github-authentication-token-expiration")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return 0.0  # 已經過期或失效
        print(f"查 GH_PAT 到期日失敗：HTTP {e.code}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001 - 查不到不影響主要功能
        print(f"查 GH_PAT 到期日失敗：{e}", file=sys.stderr)
        return None
    if not raw:
        return None
    # 格式像 "2026-12-21 16:00:00 UTC" 或 "2026-12-21 16:00:00 +0800"
    expires = datetime.strptime(raw.replace(" UTC", " +0000"), "%Y-%m-%d %H:%M:%S %z")
    return (expires - datetime.now(ZoneInfo("UTC"))).total_seconds() / 86400


def pat_warning(days_left: float | None) -> str:
    if days_left is None or days_left > PAT_WARN_DAYS:
        return ""
    if days_left <= 0:
        when = "**已經過期**"
    elif days_left < 1:
        when = "**今天就會過期**"
    else:
        when = f"再 **{days_left:.0f} 天**就會過期"
    return (f"`GH_PAT` {when}，過期後 bot 就存不了新的 Claude token。\n"
            "請到 GitHub 建一把新的，再執行\n"
            f"`gh secret set GH_PAT --repo {os.environ.get('GITHUB_REPOSITORY') or '<你的 repo>'}` 更新。")


def build_error_message(err: Exception, user_id: str, pat_days_left: float | None = None) -> dict:
    mention = f"<@{user_id}> " if user_id else ""
    fields = [{"name": "怎麼辦？",
               "value": "如果是 token 失效，照 README 的「重新登入」步驟更新 `CLAUDE_REFRESH_TOKEN`。",
               "inline": False}]
    warn = pat_warning(pat_days_left)
    if warn:
        fields.append({"name": "🔑 GH_PAT 快過期了", "value": warn, "inline": False})
    return {
        "content": f"{mention}⚠️ 今天抓不到 Claude 用量",
        "allowed_mentions": {"users": [user_id] if user_id else []},
        "embeds": [{
            "title": "發生錯誤",
            "description": f"```{str(err)[:1500]}```",
            "color": 0xE74C3C,
            "fields": fields,
            "timestamp": datetime.now(ZoneInfo("UTC")).isoformat(),
        }],
    }


def webhook_url() -> str:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise RuntimeError("沒有設定 DISCORD_WEBHOOK_URL")
    return url.split("?")[0].rstrip("/")


def send_discord(payload: dict) -> str | None:
    """發訊息，回傳新訊息的 ID（?wait=true 才會回傳訊息內容）。"""
    res = http_json(webhook_url() + "?wait=true", data=payload)
    return res.get("id")


def delete_previous_message() -> None:
    """刪掉上一則 bot 發的訊息（ID 存在 LAST_DISCORD_MESSAGE_ID 這個 Secret）。"""
    old_id = os.environ.get("LAST_DISCORD_MESSAGE_ID", "").strip()
    if not old_id:
        return
    req = urllib.request.Request(f"{webhook_url()}/messages/{old_id}", method="DELETE",
                                 headers={"User-Agent": USER_AGENT})
    try:
        urllib.request.urlopen(req, timeout=15).close()
        print("已刪除上一則訊息。")
    except urllib.error.HTTPError as e:
        # 404 = 已經被手動刪掉了，不用管
        if e.code != 404:
            print(f"刪除上一則訊息失敗：HTTP {e.code}", file=sys.stderr)


def report_day(now: datetime) -> str:
    """這次報告算哪一天的：往回推 12 小時，所以凌晨晚到的觸發還算前一天。"""
    return (now - timedelta(hours=12)).date().isoformat()


def should_skip(now: datetime) -> str | None:
    """自動觸發可能會重複或很晚才來，這裡擋掉。回傳跳過的原因，None 代表要發。"""
    if os.environ.get("FORCE") == "1":
        return None
    if not (now.hour >= 22 or now.hour < 3):
        return f"現在 {now:%H:%M} 不在 22:00–03:00 的發送時段"
    if os.environ.get("LAST_SENT_DAY", "").strip() == report_day(now):
        return f"{report_day(now)} 的報告已經發過了"
    return None


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    fake = "--fake" in sys.argv
    tz = ZoneInfo(os.environ.get("TIMEZONE") or "Asia/Taipei")
    user_id = os.environ.get("DISCORD_USER_ID", "").strip()
    now = datetime.now(tz)
    if not dry_run and (reason := should_skip(now)):
        print(f"跳過：{reason}。")
        return 0
    pat_days_left = float(os.environ["FAKE_PAT_DAYS"]) if fake and os.environ.get("FAKE_PAT_DAYS") else get_pat_days_left()
    if pat_days_left is not None:
        print(f"GH_PAT 剩 {pat_days_left:.1f} 天到期")

    try:
        if fake:
            usage = {
                "five_hour": {"utilization": 12.0, "resets_at": "2099-01-01T00:00:00+00:00"},
                "seven_day": {"utilization": 63.0, "resets_at": (datetime.now(tz) + timedelta(days=2)).replace(microsecond=0).isoformat()},
                "seven_day_opus": None,
                "seven_day_sonnet": {"utilization": 8.0, "resets_at": None},
            }
        else:
            usage = get_usage(get_access_token())
        payload = build_message(usage, user_id, tz, pat_days_left)
        ok = True
    except Exception as e:  # noqa: BLE001 - 失敗也要通知
        payload = build_error_message(e, user_id, pat_days_left)
        ok = False
        print(f"錯誤：{e}", file=sys.stderr)

    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        new_id = send_discord(payload)
        print("已發送到 Discord。")
        # 新訊息發成功之後才刪舊的，避免兩則都不見
        delete_previous_message()
        if new_id and save_secret("LAST_DISCORD_MESSAGE_ID", new_id):
            print("已記下這則訊息的 ID，下次會刪掉它。")
        if os.environ.get("FORCE") != "1":
            save_secret("LAST_SENT_DAY", report_day(now))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
