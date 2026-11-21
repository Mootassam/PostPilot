"""Rate-limit guard: each network's posting limits, live usage per account, and when the next post may go out.

Limits are counted from what PostPilot actually published (threads count every part). Networks that report their
own remaining quota (X headers, Instagram's content_publishing_limit) are respected on top of that.
"""

from __future__ import annotations

import json
from datetime import datetime

import store

DAY, HOUR, MONTH = 86400, 3600, 30 * 86400

# (max posts, window seconds, explanation)
DEFAULTS: dict[str, list[tuple[int, int, str]]] = {
    "instagram": [(100, DAY, "Instagram API: 100 posts per 24 hours")],
    "facebook": [(100, DAY, "Facebook: safe limit of 100 Page posts per 24 hours")],
    "threads": [(250, DAY, "Threads API: 250 posts per 24 hours")],
    "linkedin": [(150, DAY, "LinkedIn: 150 member posts per day")],
    "youtube": [(6, DAY, "YouTube API quota: about 6 uploads per day")],
    "pinterest": [(100, DAY, "Pinterest: safe limit of 100 pins per 24 hours")],
    "bluesky": [(1600, HOUR, "Bluesky: 1,600 posts per hour")],
    "mastodon": [(300, 3 * HOUR, "Mastodon: 300 posts per 3 hours (server default)")],
    "telegram": [(20, 60, "Telegram: 20 messages per minute in a channel")],
    "discord": [(30, 60, "Discord webhooks: 30 messages per minute")],
}
X_PLANS = {
    "free": [(17, DAY, "X Free plan: 17 posts per 24 hours"), (500, MONTH, "X Free plan: 500 posts per month")],
    "basic": [(100, DAY, "X Basic plan: 100 posts per 24 hours"), (3000, MONTH, "X Basic plan: 3,000 posts per month")],
    "pro": [(10000, DAY, "X Pro plan: 10,000 posts per 24 hours")],
}


def rules(network: str, brand_id: int | None = None) -> list[tuple[int, int, str]]:
    over = store.jd(store.get("limits")).get(network)
    if over:
        return [(int(n), int(w), f"Your limit: {int(n)} posts per {window_label(int(w))}") for n, w in over]
    if network == "x":   # each brand can have its own X plan (its own developer account)
        plan = store.bget(brand_id or store.get("brand_id"), "x_plan")
        return X_PLANS.get(plan or "free", X_PLANS["free"])
    return DEFAULTS.get(network, [])


def window_label(sec: int) -> str:
    if sec % MONTH == 0:
        return "month"
    if sec % DAY == 0:
        return "24 hours" if sec == DAY else f"{sec // DAY} days"
    if sec % HOUR == 0:
        return "hour" if sec == HOUR else f"{sec // HOUR} hours"
    return "minute" if sec == 60 else f"{sec} seconds"


def used(account_id: int, window: int, now: int | None = None) -> int:
    now = now or store.now()
    # uploads count when they were sent (a video handed to YouTube today uses today's quota, even if it goes live next week)
    return store.scalar("SELECT COALESCE(SUM(COALESCE(units,1)),0) FROM targets WHERE account_id=? AND status IN ('published','handed')"
                        " AND COALESCE(sent_at,published_at)>?", (account_id, now - window), 0)


def wait_until(account: dict, needed: int = 1) -> tuple[int | None, str]:
    """(timestamp when posting is allowed again, reason) — (None, '') when it can post now."""
    now = store.now()
    rl = store.jd(account.get("rl"))
    if rl.get("remaining") == 0 and rl.get("reset", 0) > now:
        return int(rl["reset"]) + 5, rl.get("label") or "The network says your posting limit is used up"
    for limit, window, label in rules(account["network"], store.brand_of_account(account)):
        if used(account["id"], window, now) + needed > limit:
            # the moment enough old posts leave the window
            rows = store.rows("SELECT COALESCE(sent_at,published_at) published_at, COALESCE(units,1) u FROM targets WHERE account_id=?"
                              " AND status IN ('published','handed') AND COALESCE(sent_at,published_at)>?"
                              " ORDER BY COALESCE(sent_at,published_at)", (account["id"], now - window))
            excess = used(account["id"], window, now) + needed - limit
            freed, when = 0, now + 60
            for r in rows:
                freed += r["u"]
                if freed >= excess:
                    when = r["published_at"] + window + 5
                    break
            return when, label
    return None, ""


def usage(account: dict) -> list[dict]:
    out = []
    for limit, window, label in rules(account["network"], store.brand_of_account(account)):
        out.append({"limit": limit, "window": window, "window_label": window_label(window), "used": used(account["id"], window), "label": label})
    return out


def forecast(account: dict, ts: int, extra: int = 1, exclude_post: int | None = None) -> list[str]:
    """Warnings for scheduling one more post at `ts` (counts already scheduled + published posts in each window)."""
    warns = []
    for limit, window, label in rules(account["network"], store.brand_of_account(account)):
        if window < HOUR:
            continue
        q = ("SELECT COUNT(*) FROM targets t JOIN posts p ON p.id=t.post_id WHERE t.account_id=? AND ((t.status IN ('pending','publishing')"
             " AND p.status='scheduled' AND p.scheduled_at>? AND p.scheduled_at<=?) OR (t.status='published' AND t.published_at>? AND t.published_at<=?))"
             + (" AND p.id<>?" if exclude_post else ""))
        args = [account["id"], ts - window, ts + window, ts - window, ts + window] + ([exclude_post] if exclude_post else [])
        n = store.scalar(q, args, 0) + extra
        if n > limit:
            warns.append(f"{n} posts within {window_label(window)} around this time — {label}. Extra posts wait automatically.")
    return warns


def remember(account_id: int, info: dict) -> None:
    """Store limits the network reported (e.g. X: remaining posts + reset time)."""
    if not info:
        return
    a = store.one("SELECT rl FROM accounts WHERE id=?", (account_id,))
    rl = {**store.jd(a["rl"] if a else "{}"), **info, "at": store.now()}
    store.execute("UPDATE accounts SET rl=? WHERE id=?", (json.dumps(rl), account_id))


def fmt(ts: int) -> str:
    d = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    return d.strftime("%H:%M") if d.date() == today else d.strftime("%a %d %b %H:%M")

