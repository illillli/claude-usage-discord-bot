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
# Discord API 規定要用這種格式，不然會回 40333
DISCORD_USER_AGENT = "DiscordBot (https://github.com/illillli/claude-usage-discord-bot, 1.0)"


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


def weekend_message(spare: float, free: float, resets_at: str | None) -> str:
    days_left = hours_until(to_dt(resets_at)) / 24
    msg = f"你比時間進度少用了約 **{spare:.0f}%**，可以放心多做一點事！"
    if days_left >= 1:
        msg += f"\n距離重置還有 {days_left:.1f} 天，平均每天可以用 **{free / days_left:.0f}%**。"
    else:
        msg += "\n不到一天就重置了，剩下的額度不用就浪費囉！"
    return msg


# 小討預備：每週四中午 12:00 前，先保留一週用量的 20% 不算在可自由使用的額度裡
RESERVE_PCT = 20
RESERVE_WEEKDAY = 3  # 0=週一 … 3=週四
RESERVE_HOUR = 12


def reserve_deadline(tz: ZoneInfo, reset_dt: datetime | None) -> datetime | None:
    """下一個週四 12:00；如果它在這次重置之後（不屬於這一週）就回 None，代表現在不用保留。"""
    now = datetime.now(tz)
    days_ahead = (RESERVE_WEEKDAY - now.weekday()) % 7
    deadline = (now + timedelta(days=days_ahead)).replace(hour=RESERVE_HOUR, minute=0, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=7)
    if reset_dt and deadline > reset_dt:
        return None
    return deadline


# 使用節奏等級：spare = 照時間應該用到的量 − 實際用量（正的代表比時間省、負的代表用太快）
PACE_TIERS = [
    (25, "🤩", "超級有餘裕", "額度多到用不完，放心大膽地用！"),
    (10, "🟢", "很有餘裕", "可以多開幾個任務，用起來！"),
    (3, "🍀", "稍有餘裕", "比進度省一點，維持這個步調就好。"),
    (-3, "🟡", "跟時間差不多", "步調剛剛好，照這樣用就對了。"),
    (-10, "🟠", "有點快", "稍微放慢一點會比較保險。"),
    (-25, "🔴", "用太快了", "要省著用了，先處理重要的事。"),
    (float("-inf"), "🚨", "快不夠用", "照這樣下去會提早用完，盡量只做必要的。"),
]


def pace_tier(used: float, elapsed: float, budget: float = 100) -> tuple[float, str, str, str]:
    """budget：這一週可以自由使用的總量（有小討預備時是 80）。"""
    spare = elapsed * budget / 100 - used
    # 週期剛開始（前 10% 的時間）用一點點很正常，不急著說用太快
    if elapsed < 10 and used <= 15:
        return spare, "🌱", "新的一週", "額度剛重置，盡情用吧！"
    for threshold, icon, label, advice in PACE_TIERS:
        if spare >= threshold:
            return spare, icon, label, advice
    raise AssertionError("unreachable")


def projection(used: float, elapsed: float, hours_to_reset: float, reserve: float = 0) -> str | None:
    """照目前的平均速度預估重置時會用到多少。reserve：已預扣給小討的量，直接算進去。"""
    cap = 100 - reserve  # 小討以外可以用的總量
    if reserve and used >= cap:
        return "⚠️ 可用的額度已經用完了，再用下去就會用到週四小討的份"
    if elapsed < 3 or used <= 0:
        return None  # 週期剛開始，資料太少不準
    elapsed_hours = elapsed / 100 * WEEK_SECONDS / 3600
    rate = used / elapsed_hours  # 每小時用掉幾 %
    # 週四小討前：假設小討會吃掉 reserve，其餘照目前步調；小討後 reserve=0，單純照步調
    projected = used / elapsed * 100 + reserve
    if projected <= 100:
        return f"📈 照目前速度，重置時大約會用到 **{projected:.0f}%**"
    hours_to_empty = (cap - used) / rate
    return (f"📈 照目前速度，大約 **{fmt_left(hours_to_empty)}後**會用完"
            f"（比重置早 {fmt_left(hours_to_reset - hours_to_empty)}）")


# ── 5 小時額度換算 ──────────────────────────────────────────────
# 一個 5 小時視窗用滿（100%）大約等於週額度的幾 %。Anthropic 沒公布，各方案也不同，
# 所以先用預設值，之後由 learn_ratio() 從實際數據自動校正（存在 FIVE_HOUR_RATIO 這個 Secret）。
DEFAULT_FIVE_HOUR_RATIO = 15.0
SLEEP_START, SLEEP_END = 0, 8  # 00:00–08:00 睡覺，不會用 Claude
MIN_DELTA_5H = 10  # 5 小時用量至少增加這麼多才拿來校正，太少誤差太大


def five_hour_ratio() -> float:
    try:
        r = float(os.environ.get("FIVE_HOUR_RATIO", "").strip())
        if 0.5 <= r <= 100:
            return r
    except ValueError:
        pass
    return DEFAULT_FIVE_HOUR_RATIO


def ratio_is_learned() -> bool:
    return bool(os.environ.get("FIVE_HOUR_RATIO", "").strip())


def same_time(a: str | None, b: str | None, tol_min: float = 10) -> bool:
    da, db = to_dt(a), to_dt(b)
    return bool(da and db and abs((da - db).total_seconds()) <= tol_min * 60)


def learn_ratio(usage: dict) -> tuple[dict, float | None]:
    """跟上次的快照比：同一個 5 小時視窗、同一週內，用「週用量增加 ÷ 5 小時用量增加」算比例。
    回傳 (這次的快照, 新的比例或 None)。"""
    five = usage.get("five_hour") or {}
    week = usage.get("seven_day") or {}
    snap = {"r5": five.get("resets_at"), "u5": five.get("utilization"),
            "r7": week.get("resets_at"), "u7": week.get("utilization")}
    try:
        prev = json.loads(os.environ.get("USAGE_SNAPSHOT", "") or "{}")
    except json.JSONDecodeError:
        prev = {}
    if None in (snap["u5"], snap["u7"], prev.get("u5"), prev.get("u7")):
        return snap, None
    if not (same_time(prev.get("r5"), snap["r5"]) and same_time(prev.get("r7"), snap["r7"], 60)):
        return snap, None
    d5 = float(snap["u5"]) - float(prev["u5"])
    d7 = float(snap["u7"]) - float(prev["u7"])
    if d5 < MIN_DELTA_5H or d7 < 0:
        return snap, None
    sample = d7 / d5 * 100
    if not 0.5 <= sample <= 100:
        return snap, None
    # 跟舊值做加權平均，避免一次的誤差影響太大
    new = sample if not ratio_is_learned() else five_hour_ratio() * 0.6 + sample * 0.4
    return snap, round(new, 2)


def awake_windows(now: datetime, five: dict, reset_dt: datetime | None) -> float:
    """到週重置前，醒著的時間（扣掉 00:00–08:00）最多還能用掉幾個「5 小時用滿」。
    寬鬆估計：只要視窗是在醒著時開始的就算能用滿，所以如果剩餘量比這個還多，就是確定用不完。"""
    if not reset_dt:
        return 0.0
    tz = now.tzinfo
    reset_dt = reset_dt.astimezone(tz)

    def awake(t: datetime) -> bool:
        return not (SLEEP_START <= t.hour < SLEEP_END)

    def next_awake(t: datetime) -> datetime:
        return t if awake(t) else t.replace(hour=SLEEP_END, minute=0, second=0, microsecond=0)

    count = 0.0
    t = now
    r5 = to_dt(five.get("resets_at"))
    if r5 and r5 > now:  # 現在這個 5 小時視窗還沒結束，剩下的部分也算進去
        if awake(now):
            count += max(0.0, 100 - float(five.get("utilization") or 0)) / 100
        t = r5.astimezone(tz)
    while True:
        t = next_awake(t)
        if t >= reset_dt:
            break
        count += 1
        t += timedelta(hours=5)
    return count


def surplus_info(usage: dict, tz: ZoneInfo) -> dict | None:
    """剩餘週額度換算成幾個 5 小時用滿，以及到重置前醒著最多還能用掉幾個。"""
    week = usage.get("seven_day") or {}
    reset_dt = to_dt(week.get("resets_at"))
    if not reset_dt:
        return None
    remaining = max(0.0, 100 - float(week.get("utilization") or 0))
    ratio = five_hour_ratio()
    capacity = awake_windows(datetime.now(tz), usage.get("five_hour") or {}, reset_dt)
    return {
        "ratio": ratio,
        "remaining_windows": remaining / ratio,
        "capacity": capacity,
        "cannot_finish": remaining / ratio > capacity,
        "leftover_pct": max(0.0, remaining - capacity * ratio),
    }


def surplus_lines(info: dict, free: float, after_reserve: bool) -> list[str]:
    """加在報告內文的幾行；「醒著最多用幾個」只在週四小討結算後才顯示。"""
    line = f"🧮 5 小時換算：剩 **{free / info['ratio']:.1f} 個**"
    if after_reserve:
        line += f" / 最多用 **{info['capacity']:.1f} 個**"
    lines = [line]
    if info["cannot_finish"]:
        lines.append(f"🔥 用不完，會剩約 {info['leftover_pct']:.0f}%")
    return lines


def build_surplus_ping(info: dict, user_id: str) -> dict:
    return {
        "content": (f"<@{user_id}> 🔥 **這週額度確定用不完！**"
                    f"剩 {info['remaining_windows']:.1f} 個 5 小時、最多只能用 {info['capacity']:.1f} 個，大膽開任務吧！"),
        "allowed_mentions": {"users": [user_id] if user_id else []},
    }


def usage_bar(free: float, reserved: float) -> str:
    """🟩/🟨/🟥 可自由使用、🟪 小討預備、⬛ 已用掉。"""
    fb = max(0, min(BLOCKS, round(free / 100 * BLOCKS)))
    rb = max(0, min(BLOCKS - fb, round(reserved / 100 * BLOCKS)))
    return level_square(free) * fb + "🟪" * rb + "⬛" * (BLOCKS - fb - rb)


def build_message(usage: dict, user_id: str, tz: ZoneInfo, pat_days_left: float | None = None,
                  is_noon: bool = False, manual: bool = False) -> dict:
    week = usage.get("seven_day") or {}
    used = float(week.get("utilization") or 0)
    remaining = max(0.0, 100 - used)
    reset_dt = to_dt(week.get("resets_at"))
    deadline = reserve_deadline(tz, reset_dt)
    reserved = min(RESERVE_PCT, remaining) if deadline else 0.0
    free = remaining - reserved
    budget = 100 - RESERVE_PCT if deadline else 100
    color = 0x2ECC71 if free >= 50 else 0xF1C40F if free >= 20 else 0xE74C3C

    elapsed = time_elapsed_pct(week.get("resets_at"))
    if deadline:
        lines = [
            "**📅 7 天總用量**",
            f"{usage_bar(free, reserved)}  **{free:.0f}%** 可用",
            f"-# 已用 {used:.0f}%・🟪 已預扣 {reserved:.0f}% 給週四小討",
        ]
    else:
        lines = [
            "**📅 7 天總用量**",
            f"{usage_bar(free, reserved)}  **{remaining:.0f}%** 剩餘",
            f"-# 已用 {used:.0f}%",
        ]

    spare = None
    if elapsed is not None:
        spare, pace_icon, pace_label, pace_advice = pace_tier(used, elapsed, budget)
        diff = (f"比時間進度省了 {spare:.0f}%" if spare >= 0.5
                else f"比時間進度多用了 {-spare:.0f}%" if spare <= -0.5 else "和時間進度一樣")
        if deadline:
            diff += f"（已扣掉小討的 {RESERVE_PCT}%）"
        lines += [
            "",
            "**⏳ 時間進度**",
            f"{emoji_bar(elapsed, '🟦')}  **{elapsed:.0f}%** 已過",
            f"-# 離重置還有 {fmt_left(hours_until(reset_dt))}",
            "",
            f"**🧭 使用節奏**　{pace_icon} **{pace_label}**",
            pace_advice,
            f"-# {diff}",
        ]
        proj = projection(used, elapsed, hours_until(reset_dt), RESERVE_PCT if deadline else 0)
        if proj:
            lines.append(proj)

    fields = []
    for key, label in [("seven_day_sonnet", "🎼 Sonnet 週額度"), ("seven_day_opus", "💎 Opus 週額度")]:
        w = usage.get(key)
        if w and w.get("utilization") is not None:
            fields.append({"name": label, "value": f"剩 **{100 - float(w['utilization']):.0f}%**", "inline": True})
    fields.append({"name": "🔄 下次重置", "value": discord_time(reset_dt), "inline": False})

    info = surplus_info(usage, tz)
    if info and ((spare is not None and spare >= 10) or info["cannot_finish"]):  # 很有餘裕以上才換算
        lines += ["", *surplus_lines(info, free, after_reserve=not deadline)]

    if spare is not None and spare >= 3 and weekday_now(tz) in (4, 5):  # 週五、週六，而且有餘裕
        fields.append({"name": "🎉 週末加碼",
                       "value": weekend_message(spare, free, week.get("resets_at")),
                       "inline": False})
    warn = pat_warning(pat_days_left)
    if warn:
        fields.append({"name": "🔑 GH_PAT 快過期了", "value": warn, "inline": False})

    title = (f"本週可用 {free:.0f}%（已預扣小討 {reserved:.0f}%）" if deadline
             else f"本週還剩 {remaining:.0f}%")
    if manual:  # 自己下 /usage 指令，不用再 @ 自己
        user_id = ""
    mention = f"<@{user_id}> " if user_id else ""
    if manual:
        content = "🔄 剛剛更新的 Claude 用量"
    elif is_noon:
        content = f"{mention}☀️ 午安！中午的 Claude 用量報告來了"
    else:
        content = f"{mention}🌙 晚安！今天的 Claude 用量報告來了"
    return {
        "content": content,
        "allowed_mentions": {"users": [user_id] if user_id else []},
        "embeds": [{
            "author": {"name": "Claude 用量小幫手"},
            "title": title,
            "description": "\n".join(lines),
            "color": color,
            "fields": fields,
            "footer": {"text": "每天 12:00、23:00 自動回報"},
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
    # 可能有多則（報告 + 用不完的額外提醒），用逗號分隔
    for old_id in filter(None, (s.strip() for s in os.environ.get("LAST_DISCORD_MESSAGE_ID", "").split(","))):
        req = urllib.request.Request(f"{webhook_url()}/messages/{old_id}", method="DELETE",
                                     headers={"User-Agent": USER_AGENT})
        try:
            urllib.request.urlopen(req, timeout=15).close()
            print("已刪除上一則訊息。")
        except urllib.error.HTTPError as e:
            # 404 = 已經被手動刪掉了，不用管
            if e.code != 404:
                print(f"刪除上一則訊息失敗：HTTP {e.code}", file=sys.stderr)


def reply_interaction(content: str) -> None:
    """回覆 Discord /usage 指令（把「思考中…」那則改成結果，只有下指令的人看得到）。"""
    token = os.environ.get("INTERACTION_TOKEN", "").strip()
    app_id = os.environ.get("DISCORD_APPLICATION_ID", "").strip()
    if not (token and app_id):
        return
    url = f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original"
    req = urllib.request.Request(url, data=json.dumps({"content": content}).encode(), method="PATCH",
                                 headers={"User-Agent": DISCORD_USER_AGENT, "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).close()
    except urllib.error.HTTPError as e:
        # 互動 token 15 分鐘就過期，回不了也不影響報告本身
        print(f"回覆 /usage 指令失敗：HTTP {e.code}", file=sys.stderr)


def report_day(now: datetime) -> str:
    """這次報告算哪一天的：往回推 12 小時，所以凌晨晚到的觸發還算前一天。"""
    return (now - timedelta(hours=12)).date().isoformat()


# 發送時段：中午 12:00（11:00–15:00 內都算）、晚上 23:00（22:00–03:00 內都算）
def slot_name(now: datetime) -> str | None:
    if 11 <= now.hour < 15:
        return "noon"
    if now.hour >= 22 or now.hour < 3:
        return "night"
    return None


def report_slot(now: datetime) -> str | None:
    """這次報告的唯一代號，例如 2026-11-24-noon、2026-11-24-night，用來防止重複發送。"""
    slot = slot_name(now)
    if slot == "noon":
        return f"{now.date().isoformat()}-noon"
    if slot == "night":
        return f"{report_day(now)}-night"
    return None


def should_skip(now: datetime) -> str | None:
    """自動觸發可能會重複或很晚才來，這裡擋掉。回傳跳過的原因，None 代表要發。"""
    if os.environ.get("FORCE") == "1":
        return None
    key = report_slot(now)
    if key is None:
        return f"現在 {now:%H:%M} 不在發送時段（11:00–15:00、22:00–03:00）"
    if os.environ.get("LAST_SENT_SLOT", "").strip() == key:
        return f"{key} 的報告已經發過了"
    return None


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    fake = "--fake" in sys.argv
    tz = ZoneInfo(os.environ.get("TIMEZONE") or "Asia/Taipei")
    user_id = os.environ.get("DISCORD_USER_ID", "").strip()
    now = datetime.now(tz)
    mask(os.environ.get("INTERACTION_TOKEN", "").strip())
    manual = bool(os.environ.get("INTERACTION_TOKEN", "").strip()) or os.environ.get("FAKE_MANUAL") == "1"
    if not dry_run and (reason := should_skip(now)):
        print(f"跳過：{reason}。")
        return 0
    pat_days_left = float(os.environ["FAKE_PAT_DAYS"]) if fake and os.environ.get("FAKE_PAT_DAYS") else get_pat_days_left()
    if pat_days_left is not None:
        print(f"GH_PAT 剩 {pat_days_left:.1f} 天到期")

    try:
        if fake:
            fake_used = float(os.environ.get("FAKE_USED") or 63)
            fake_days = float(os.environ.get("FAKE_DAYS_LEFT") or 2)
            usage = {
                "five_hour": {"utilization": 12.0, "resets_at": (datetime.now(tz) + timedelta(hours=3)).replace(microsecond=0).isoformat()},
                "seven_day": {"utilization": fake_used, "resets_at": (datetime.now(tz) + timedelta(days=fake_days)).replace(microsecond=0).isoformat()},
                "seven_day_opus": None,
                "seven_day_sonnet": {"utilization": 8.0, "resets_at": None},
            }
        else:
            usage = get_usage(get_access_token())
        # 手動 force 或時段外執行時，就看現在是白天還是晚上
        is_noon = (slot_name(now) or ("noon" if 6 <= now.hour < 18 else "night")) == "noon"
        snapshot, new_ratio = (None, None) if fake else learn_ratio(usage)
        if new_ratio is not None:
            print(f"5 小時額度比例校正：{five_hour_ratio():.2f}% → {new_ratio:.2f}%")
            os.environ["FIVE_HOUR_RATIO"] = str(new_ratio)  # 這次的報告就用新的值
        payload = build_message(usage, user_id, tz, pat_days_left, is_noon, manual)
        info = surplus_info(usage, tz)
        # 確定用不完時，自動報告多發一則再 @ 一次（自己下 /usage 就不用了）
        ping = build_surplus_ping(info, user_id) if info and info["cannot_finish"] and not manual else None
        ok = True
    except Exception as e:  # noqa: BLE001 - 失敗也要通知
        payload = build_error_message(e, user_id, pat_days_left)
        snapshot = new_ratio = ping = None
        ok = False
        print(f"錯誤：{e}", file=sys.stderr)

    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if ping:
            print(json.dumps(ping, ensure_ascii=False, indent=2))
    else:
        new_ids = [send_discord(payload)]
        if ping:
            new_ids.append(send_discord(ping))
        print("已發送到 Discord。")
        # 新訊息發成功之後才刪舊的，避免兩則都不見
        delete_previous_message()
        new_ids = [i for i in new_ids if i]
        if new_ids and save_secret("LAST_DISCORD_MESSAGE_ID", ",".join(new_ids)):
            print("已記下這則訊息的 ID，下次會刪掉它。")
        if snapshot:
            save_secret("USAGE_SNAPSHOT", json.dumps(snapshot))
        if new_ratio is not None:
            save_secret("FIVE_HOUR_RATIO", str(new_ratio))
        if os.environ.get("FORCE") != "1" and report_slot(now):
            save_secret("LAST_SENT_SLOT", report_slot(now))
        reply_interaction("✅ 已更新，最新的用量報告在頻道裡。" if ok
                          else "⚠️ 抓不到用量，錯誤訊息已經發在頻道裡。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
