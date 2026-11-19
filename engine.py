"""The background engine: publishes due posts (in parallel, one at a time per account), retries with backoff,
recurring posts, the posting queue (time slots), RSS auto-posting, stats refresh and token renewal."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html import unescape

import connectors
import inbox as inbox_mod
import limits
import media as media_mod
import net
import store
from connectors.base import add_utm
from net import PublishError

BACKOFF = [60, 300, 900, 1800, 3600]
PORT = 5088
PUBLIC: dict[str, str] = {}          # public token → file path (served at /m/<token>/<name> while needed)
_public_lock = threading.Lock()
_tunnel_last_use = 0.0


# ------------------------------------------------------------------ public media links (Instagram / Threads)
def public_base() -> str:
    global _tunnel_last_use
    mode = store.get("media_host") or "auto"
    if mode == "custom":
        base = (store.get("public_base") or "").rstrip("/")
        if not base.startswith("https://"):
            raise PublishError("Set your public https address in Settings → Media hosting (Instagram/Threads need it).")
        return base
    if mode == "off":
        raise PublishError("Instagram/Threads need a public media link — turn on Media hosting in Settings.")
    import tunnel
    _tunnel_last_use = time.time()
    st = tunnel.status()
    if st["status"] != "on":
        tunnel.start(PORT)
        end = time.time() + 120
        while time.time() < end:
            st = tunnel.status()
            if st["status"] == "on":
                time.sleep(4)  # give Cloudflare's edge a moment to route the new address
                break
            if st["status"] == "error":
                raise PublishError(f"Couldn't open the secure media link: {st['error']}", "transient")
            time.sleep(1)
        else:
            raise PublishError("The secure media link did not start in time — will retry.", "transient")
    _tunnel_last_use = time.time()
    return st["url"]


def public_url(path: str) -> str:
    base = public_base()
    tok = secrets.token_urlsafe(18)
    with _public_lock:
        PUBLIC[tok] = path
    return f"{base}/m/{tok}/{os.path.basename(path)}"


# ------------------------------------------------------------------ building what a connector receives
def account_out(a: dict) -> dict:
    a = dict(a)
    a["extra_d"] = store.jd(a.get("extra"))
    return a


def build(post: dict, target: dict, account: dict) -> dict:
    conn = connectors.get(account["network"])
    text = (target["text"] or post["text"] or "").strip()
    opts = store.jd(target["options"])
    link = (post.get("link") or "").strip()
    if link and post.get("utm"):
        link = add_utm(link, account["network"], store.get("utm_source"), store.get("utm_medium"), store.get("utm_campaign"))
    text_no_link = text
    if link and link not in text:
        text = f"{text}\n\n{link}".strip()
    items = []
    for mid in store.jl(post["media"]):
        m = store.one("SELECT * FROM media WHERE id=?", (mid,))
        if not m or not os.path.exists(m["path"]):
            raise PublishError("A media file of this post was deleted from the library.")
        path = media_mod.prepare(m, account["network"])
        w, h = (m["width"], m["height"]) if path == m["path"] else media_mod.dims(path)
        alt = (opts.get("alt") or {}).get(str(mid)) or m.get("alt") or ""
        items.append({"id": m["id"], "path": path, "kind": m["kind"], "mime": "image/jpeg" if path.endswith(".jpg") and m["kind"] == "image" else m["mime"],
                      "name": os.path.basename(path), "alt": alt, "width": w, "height": h})
    return {"text": text, "text_no_link": text_no_link, "link": link, "media": items, "options": opts,
            "first_comment": (opts.get("first_comment") or post.get("first_comment") or "") if "first_comment" in conn.supports else "",
            "public_url": lambda m: public_url(m["path"]), "target_id": target["id"], "post_id": post["id"]}


def validate_post(post: dict, targets: list[dict]) -> dict:
    """{account_id: [problems]} for the composer, without publishing anything."""
    out = {}
    for t in targets:
        a = store.one("SELECT * FROM accounts WHERE id=?", (t["account_id"],))
        if not a:
            continue
        conn = connectors.get(a["network"])
        text = (t.get("text") or post.get("text") or "").strip()
        link = post.get("link") or ""
        if link and link not in text:
            text = f"{text}\n\n{link}".strip()
        items = [store.one("SELECT * FROM media WHERE id=?", (mid,)) for mid in post.get("media") or []]
        items = [{"kind": m["kind"]} for m in items if m]
        errs = conn.validate({"text": text, "media": items, "options": t.get("options") or {}})
        if not text and not items:
            errs.append("Write something or add media.")
        if a["status"] == "expired":
            errs.append("This account's login expired — reconnect it in Accounts.")
        out[str(a["id"])] = errs
    return out


# ------------------------------------------------------------------ queue (time slots)
def slots_of(account: dict | None) -> list[dict]:
    s = store.jl(account.get("slots")) if account and account.get("slots") else None
    return s or store.jl(store.get("default_slots"))


def next_free_slot(account_ids: list[int], after: float | None = None, exclude_post: int | None = None) -> int:
    after = after or time.time()
    accounts = [store.one("SELECT * FROM accounts WHERE id=?", (i,)) for i in account_ids] or [None]
    slots = []
    for a in accounts:
        slots += slots_of(a)
    if not slots:
        slots = [{"days": "0123456", "time": "10:00"}]
    q = ("SELECT DISTINCT p.scheduled_at FROM posts p JOIN targets t ON t.post_id=p.id WHERE p.status='scheduled' AND p.scheduled_at>=?"
         + (f" AND t.account_id IN ({','.join('?' * len(account_ids))})" if account_ids else "") + (" AND p.id<>?" if exclude_post else ""))
    args = [int(after) - 3600] + list(account_ids) + ([exclude_post] if exclude_post else [])
    taken = [r["scheduled_at"] for r in store.rows(q, args)]
    start = datetime.fromtimestamp(after).replace(second=0, microsecond=0)
    for d in range(0, 90):
        day = (start + timedelta(days=d)).date()
        times = sorted({s["time"] for s in slots if str(day.weekday()) in (s.get("days") or "0123456")})
        for t in times:
            hh, mm = (int(x) for x in t.split(":"))
            ts = int(datetime(day.year, day.month, day.day, hh, mm).timestamp())
            if ts <= after + 60:
                continue
            if any(abs(ts - x) < 600 for x in taken):
                continue
            return ts
    return int(after + 3600)


BEST_DEFAULT = {  # research-based starting points (local time), refined with your own stats
    "facebook": [9, 13, 15], "instagram": [11, 13, 19], "x": [9, 12, 17], "linkedin": [8, 10, 12], "threads": [9, 12, 19],
    "youtube": [15, 17, 20], "pinterest": [20, 21, 14], "telegram": [9, 13, 19], "bluesky": [9, 12, 18], "mastodon": [10, 14, 19],
    "discord": [18, 20, 21], "reminder": [12, 18, 20], "demo": [10, 14, 18],
}


def best_times(networks: list[str]) -> list[dict]:
    """Best hours for these networks: learned from your engagement (≥ 8 posts with stats), else defaults."""
    out = []
    for n in networks:
        rows = store.rows("SELECT t.published_at, t.metrics FROM targets t JOIN accounts a ON a.id=t.account_id WHERE a.network=?"
                          " AND t.status='published' AND t.metrics_at IS NOT NULL", (n,))
        by_hour: dict[int, list[float]] = {}
        for r in rows:
            m = store.jd(r["metrics"])
            eng = (m.get("likes") or 0) + 2 * (m.get("comments") or 0) + 3 * (m.get("shares") or 0)
            by_hour.setdefault(datetime.fromtimestamp(r["published_at"]).hour, []).append(eng)
        if len(rows) >= 8 and len(by_hour) >= 3:
            ranked = sorted(by_hour, key=lambda h: sum(by_hour[h]) / len(by_hour[h]), reverse=True)[:3]
            out.append({"network": n, "hours": ranked, "learned": True})
        else:
            out.append({"network": n, "hours": BEST_DEFAULT.get(n, [10, 14, 18]), "learned": False})
    return out


# ------------------------------------------------------------------ the engine
class Engine:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="publish")
        self.busy_accounts: set[int] = set()
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self.next_feeds = self.next_metrics = self.next_tokens = 0.0
        self.next_handoff = time.time() + 15
        self.next_inbox = time.time() + 20
        self.next_health = time.time() + 90
        self.inbox_busy = self.health_busy = False
        self.last_error = ""

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="engine").start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - never die
                self.last_error = str(e)
                traceback.print_exc()
            self._stop.wait(2)

    def tick(self):
        self.dispatch()
        self.go_live()
        now = time.time()
        if now >= self.next_handoff:
            self.next_handoff = now + 60
            self.handoff()
        if now >= self.next_feeds:
            self.next_feeds = now + 60
            self.check_feeds()
        if now >= self.next_metrics:
            self.next_metrics = now + 600
            self.refresh_metrics()
        if now >= self.next_tokens:
            self.next_tokens = now + 3600
            self.refresh_tokens()
        if now >= self.next_inbox and not self.inbox_busy:
            self.next_inbox = now + 45
            threading.Thread(target=self._bg, args=(self.sync_inbox,), daemon=True, name="inbox").start()
        if now >= self.next_health and not self.health_busy:
            self.next_health = now + 600
            if self.health_due():
                threading.Thread(target=self._bg, args=(self.check_health,), daemon=True, name="health").start()
        if _tunnel_last_use and now - _tunnel_last_use > 900:
            self._stop_tunnel()

    def _bg(self, fn, *a):
        try:
            fn(*a)
        except Exception:  # noqa: BLE001 - background work must never kill the engine
            traceback.print_exc()

    def _stop_tunnel(self):
        global _tunnel_last_use
        import tunnel
        if tunnel.status()["status"] == "on" and not any(self.busy_accounts):
            tunnel.stop()
            with _public_lock:
                PUBLIC.clear()
            _tunnel_last_use = 0.0

    # --- publishing
    def due_targets(self, limit=20) -> list[dict]:
        now = store.now()
        return store.rows("SELECT t.* FROM targets t JOIN posts p ON p.id=t.post_id WHERE p.status='scheduled' AND p.scheduled_at<=?"
                          " AND t.status='pending' AND COALESCE(t.next_try_at,0)<=? ORDER BY p.scheduled_at, t.id LIMIT ?", (now, now, limit))

    def dispatch(self, wait: bool = False):
        futures = []
        for t in self.due_targets():
            with self.lock:
                if t["account_id"] in self.busy_accounts:
                    continue
                # claim atomically: if another thread already took this target, skip it
                if not store.update("UPDATE targets SET status='publishing' WHERE id=? AND status='pending'", (t["id"],)):
                    continue
                self.busy_accounts.add(t["account_id"])
            futures.append(self.pool.submit(self._publish_safe, t["id"]))
        if wait:
            for f in futures:
                f.result()
            self.wait_idle()

    # --- network-side scheduling (YouTube publishAt, Facebook scheduled_publish_time)
    def handoff_candidates(self, limit: int = 40) -> list[dict]:
        """Scheduled targets that their network can publish by itself, in its accepted window, not tried natively yet."""
        if not store.get("native_schedule"):
            return []
        now = store.now()
        rows = store.rows("SELECT t.*, p.scheduled_at sched, a.network, a.status acc_status FROM targets t JOIN posts p ON p.id=t.post_id"
                          " JOIN accounts a ON a.id=t.account_id WHERE p.status='scheduled' AND t.status='pending' AND p.scheduled_at>?"
                          " AND COALESCE(t.next_try_at,0)<=? AND COALESCE(t.native_skip,0)=0 ORDER BY p.scheduled_at, t.id LIMIT ?",
                          (now, now, limit))
        out = []
        for t in rows:
            c = connectors.REGISTRY.get(t["network"])
            win = getattr(c, "native_schedule", None) if c else None
            if win and t["acc_status"] == "ok" and win[0] <= t["sched"] - now <= win[1]:
                out.append(t)
        return out

    def handoff(self, wait: bool = False) -> None:
        futures = []
        for t in self.handoff_candidates():
            with self.lock:
                if t["account_id"] in self.busy_accounts:
                    continue
                if not store.update("UPDATE targets SET status='publishing' WHERE id=? AND status='pending'", (t["id"],)):
                    continue
                self.busy_accounts.add(t["account_id"])
            futures.append(self.pool.submit(self._publish_safe, t["id"], True))
        if wait:
            for f in futures:
                f.result()

    def go_live(self) -> None:
        """Handed-off posts whose time has come are live on the network now."""
        rows = store.rows("SELECT id, post_id FROM targets WHERE status='handed' AND published_at<=?", (store.now(),))
        for r in rows:
            store.execute("UPDATE targets SET status='published' WHERE id=? AND status='handed'", (r["id"],))
        for pid in {r["post_id"] for r in rows}:
            self.finalize(pid)

    def wait_idle(self, timeout: float = 60) -> None:
        """Block until nothing is being published (used by tests and shutdown)."""
        end = time.time() + timeout
        while time.time() < end and (self.busy_accounts or store.scalar("SELECT COUNT(*) FROM targets WHERE status='publishing'", (), 0)):
            time.sleep(0.05)

    def _publish_safe(self, target_id: int, native: bool = False):
        t = store.one("SELECT * FROM targets WHERE id=?", (target_id,))
        try:
            self.publish_target(target_id, native)
        except Exception as e:  # noqa: BLE001 - a bug must not leave a post stuck in "publishing"
            traceback.print_exc()
            if native:  # never lose a post over the early upload: fall back to publishing at its time
                self._native_back(t, skip=True)
                return
            store.execute("UPDATE targets SET status='failed', error=? WHERE id=?", (f"Unexpected error: {e}", target_id))
            self.finalize(t["post_id"])
        finally:
            with self.lock:
                self.busy_accounts.discard(t["account_id"])

    def _native_back(self, t: dict, skip: bool = False, retry_at: int | None = None) -> None:
        """Return a handoff attempt to the normal queue (it then publishes at its time as usual)."""
        store.execute("UPDATE targets SET status='pending', native_skip=?, next_try_at=? WHERE id=?",
                      (1 if skip else int(t.get("native_skip") or 0), retry_at, t["id"]))

    def publish_target(self, target_id: int, native: bool = False) -> None:
        t = store.one("SELECT * FROM targets WHERE id=?", (target_id,))
        post = store.one("SELECT * FROM posts WHERE id=?", (t["post_id"],))
        a = store.one("SELECT * FROM accounts WHERE id=?", (t["account_id"],))
        if not a:
            store.execute("UPDATE targets SET status='failed', error='The account was removed.' WHERE id=?", (target_id,))
            return self.finalize(post["id"])
        conn = connectors.get(a["network"])
        label = f"{conn.name} · {a['name']}"
        sched = int(post["scheduled_at"] or 0)
        try:
            if a["status"] == "expired":
                raise PublishError("This account's login expired — reconnect it in Accounts.", "auth")
            payload = build(post, t, a)
            errs = conn.validate(payload)
            if errs:
                raise PublishError(" ".join(errs))
            if native:
                win = conn.native_schedule
                if post["status"] != "scheduled" or not win or not conn.can_native(payload) or not (win[0] <= sched - store.now() <= win[1]):
                    return self._native_back(t, skip=bool(win) and not conn.can_native(payload))
                payload["publish_at"] = sched
                # quota full (e.g. YouTube ≈ 6 uploads/day): try again later — at the latest it publishes normally at its own time
                wait, _why = limits.wait_until(a)
                if wait:
                    return self._native_back(t, retry_at=min(int(wait), sched))
            wait, why = limits.wait_until(a)
            if wait:
                return self._defer(t, a, label, wait, why)
            creds = store.creds_of(a)
            try:
                conn.precheck(account_out(a), creds)
            except PublishError as e:
                if e.kind == "rate":
                    return self._defer(t, a, label, store.now() + int(e.retry_after or 3600), str(e))
                raise
            res = conn.publish(account_out(a), creds, payload)
        except PublishError as e:
            if native and e.kind not in ("auth", "transient", "rate"):
                # the network refused its own scheduling for this post: publish it the normal way at its time
                store.log("publish", f"{label}: couldn't hand it to {conn.name}'s scheduler ({e}) — PostPilot will publish it at the time instead.", "warn")
                return self._native_back(t, skip=True)
            if native and e.kind in ("transient", "rate"):
                return self._native_back(t, retry_at=min(store.now() + 900, sched))
            return self._failed(t, post, a, label, e)
        now = store.now()
        if native:
            store.execute("UPDATE targets SET status='handed', remote_id=?, url=?, error=?, published_at=?, sent_at=?, next_try_at=NULL, units=?,"
                          " limit_note='' WHERE id=?", (res.get("remote_id", ""), res.get("url", ""), res.get("warning", ""), sched, now,
                                                        max(1, int(res.get("units") or 1)), target_id))
            limits.remember(a["id"], res.get("rate") or {})
            store.log("publish", f"📅 Uploaded to {label} — {conn.name} publishes it at {limits.fmt(sched)} by itself (your PC can be off).", "good")
            return None
        status = "reminder" if res.get("reminder") else "published"
        store.execute("UPDATE targets SET status=?, remote_id=?, url=?, error=?, published_at=?, sent_at=?, next_try_at=NULL, units=?, limit_note=''"
                      " WHERE id=?", (status, res.get("remote_id", ""), res.get("url", ""), res.get("warning", ""), now, now,
                                      max(1, int(res.get("units") or 1)), target_id))
        limits.remember(a["id"], res.get("rate") or {})
        if res.get("warning"):
            store.log("publish", f"⚠ {label}: {res['warning']}", "warn")
        if status == "reminder":
            store.log("reminder", f"⏰ Time to post on {a['name']}: “{(post['text'] or '')[:60]}”", "notify")
        else:
            store.log("publish", f"Published on {label}", "good")
        self.finalize(post["id"])

    def _defer(self, t, a, label, when: int, why: str) -> None:
        """Rate-limit guard: keep the post queued until the network allows it (does not count as a failed attempt)."""
        note = f"Waiting — {why}. Goes out at {limits.fmt(when)}."
        store.execute("UPDATE targets SET status='pending', next_try_at=?, error=?, limit_note=? WHERE id=?", (when, note, why, t["id"]))
        if not t.get("limit_note"):
            store.log("limits", f"⏸ {label}: {note}", "warn")

    def _failed(self, t, post, a, label, e: PublishError):
        attempts = t["attempts"] + 1
        retry = (e.kind == "transient" and attempts < max(1, store.get("retry_max"))) or (e.kind == "rate" and attempts < 12)
        if retry:
            wait = e.retry_after if e.retry_after and e.retry_after < 86400 else BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
            store.execute("UPDATE targets SET status='pending', attempts=?, next_try_at=?, error=? WHERE id=?",
                          (attempts, store.now() + int(wait), f"Retry {attempts}: {e}", t["id"]))
            store.log("publish", f"{label}: {e} — retrying in {int(wait // 60) or 1} min", "warn")
            return
        store.execute("UPDATE targets SET status='failed', attempts=?, error=? WHERE id=?", (attempts, str(e), t["id"]))
        if e.kind == "auth":
            store.execute("UPDATE accounts SET status='expired', error=? WHERE id=?", (str(e), a["id"]))
        store.log("publish", f"❌ Failed on {label}: {e}", "error")
        self.finalize(post["id"])

    def finalize(self, post_id: int) -> None:
        ts = store.rows("SELECT status FROM targets WHERE post_id=?", (post_id,))
        if not ts or any(t["status"] in ("pending", "publishing", "handed") for t in ts):
            return
        ok = sum(t["status"] in ("published", "reminder", "skipped") for t in ts)
        bad = sum(t["status"] == "failed" for t in ts)
        status = "published" if not bad else ("failed" if not ok else "partial")
        post = store.one("SELECT * FROM posts WHERE id=?", (post_id,))
        if post["status"] != "scheduled":
            return
        store.execute("UPDATE posts SET status=?, published_at=?, updated_at=? WHERE id=?", (status, store.now(), store.now(), post_id))
        if status != "failed":
            self._recur(post)

    def _recur(self, post: dict) -> None:
        rec = store.jd(post.get("recurring"))
        every = float(rec.get("every_days") or 0)
        if every <= 0 or rec.get("cloned"):
            return
        done = int(rec.get("done") or 0) + 1
        times = int(rec.get("times") or 0)
        store.execute("UPDATE posts SET recurring=? WHERE id=?", (json.dumps({**rec, "cloned": True, "done": done}), post["id"]))
        if times and done >= times:
            return
        nxt = (post["scheduled_at"] or store.now()) + int(every * 86400)
        while nxt <= store.now():
            nxt += int(every * 86400)
        pid = store.execute("INSERT INTO posts(brand_id,status,text,media,first_comment,link,utm,labels,scheduled_at,recurring,source,notes,created_at,updated_at)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (post["brand_id"], "scheduled", post["text"], post["media"], post["first_comment"], post["link"], post["utm"],
                             post["labels"], nxt, json.dumps({"every_days": every, "times": times, "done": done}), "recurring",
                             post["notes"], store.now(), store.now()))
        for t in store.rows("SELECT * FROM targets WHERE post_id=?", (post["id"],)):
            store.execute("INSERT INTO targets(post_id,account_id,text,options) VALUES(?,?,?,?)", (pid, t["account_id"], t["text"], t["options"]))
        store.log("recurring", f"Recurring post re-scheduled for {datetime.fromtimestamp(nxt):%d %b %H:%M}.")

    # --- metrics
    def refresh_metrics(self, limit: int = 15, min_age: int = 300) -> int:
        now = store.now()
        rows = store.rows("SELECT t.*, a.network FROM targets t JOIN accounts a ON a.id=t.account_id WHERE t.status='published' AND t.remote_id<>''"
                          " AND t.published_at>=? AND t.published_at<=?"
                          # fresh posts change fast: every 30 min for the first 2 days, then every 6 h
                          " AND COALESCE(t.metrics_at,0) < CASE WHEN t.published_at>=? THEN ? ELSE ? END"
                          " ORDER BY COALESCE(t.metrics_at,0) LIMIT ?",
                          (now - 30 * 86400, now - min_age, now - 2 * 86400, now - 1800, now - 6 * 3600, limit))
        n = 0
        for t in rows:
            a = store.one("SELECT * FROM accounts WHERE id=?", (t["account_id"],))
            try:
                m = connectors.get(a["network"]).metrics(account_out(a), store.creds_of(a), t["remote_id"])
            except (PublishError, KeyError, ValueError, TypeError):
                m = None
            except Exception:  # noqa: BLE001 - stats are optional
                m = None
            store.execute("UPDATE targets SET metrics=COALESCE(?,metrics), metrics_at=? WHERE id=?",
                          (json.dumps(m) if m else None, now, t["id"]))
            n += 1 if m else 0
            time.sleep(0.3)
        return n

    # --- tokens
    def refresh_tokens(self) -> None:
        now = store.now()
        for a in store.rows("SELECT * FROM accounts WHERE expires_at IS NOT NULL AND expires_at < ?", (now + 7 * 86400,)):
            conn = connectors.get(a["network"])
            try:
                new = conn.refresh(account_out(a), store.creds_of(a), store.app_creds(connectors.app_key(a["network"])))
            except PublishError:
                new = None
            if new:
                exp = new.pop("expires_at", None)
                store.save_creds(a["id"], {**store.creds_of(a), **new})
                store.execute("UPDATE accounts SET expires_at=?, status='ok', error='' WHERE id=?", (exp, a["id"]))
                store.log("accounts", f"Renewed the login of {a['name']} automatically.")
            elif a["expires_at"] < now:
                store.execute("UPDATE accounts SET status='expired', error='Login expired — click Reconnect.' WHERE id=?", (a["id"],))
                self._expiry_note(a, -1)
            else:
                days = max(1, -(-(a["expires_at"] - now) // 86400))
                store.execute("UPDATE accounts SET error=? WHERE id=?", (f"Login expires in {days} day(s) — reconnect soon.", a["id"]))
                self._expiry_note(a, days)

    def _expiry_note(self, a: dict, days: int) -> None:
        """One notification per milestone (7, 3, 1 day, expired) — never spam."""
        bucket = "expired" if days < 0 else ("1" if days <= 1 else "3" if days <= 3 else "7" if days <= 7 else "")
        if not bucket or a.get("expiry_notified") == f"{a['expires_at']}:{bucket}":
            return
        store.execute("UPDATE accounts SET expiry_notified=? WHERE id=?", (f"{a['expires_at']}:{bucket}", a["id"]))
        net_name = connectors.get(a["network"]).name
        if bucket == "expired":
            store.log("accounts", f"❌ The {net_name} login of {a['name']} expired — open Accounts and click Reconnect.", "error")
        else:
            store.log("accounts", f"⏳ The {net_name} login of {a['name']} expires in {days} day(s) — reconnect it in Accounts to avoid missed posts.", "notify")

    # --- daily account health test
    def health_due(self) -> bool:
        now = datetime.now()
        if store.get("health_last_day") != now.strftime("%Y-%m-%d") and now.hour >= store.get("health_hour"):
            return True
        stale = store.scalar("SELECT COUNT(*) FROM accounts WHERE status<>'paused' AND network NOT IN ('reminder') AND "
                             "COALESCE(last_check_at,0) < ?", (store.now() - 30 * 3600,), 0)
        return bool(stale)

    def check_health(self, account_ids: list[int] | None = None) -> dict:
        """Quiet login test of every account. Dead logins are marked and announced; warnings are kept on the account."""
        self.health_busy = True
        summary = {"healthy": 0, "dead": 0, "warn": 0, "skipped": 0}
        try:
            q = "SELECT * FROM accounts WHERE status<>'paused'"
            rows = store.rows(q)
            if account_ids:
                rows = [r for r in rows if r["id"] in set(account_ids)]
            for a in rows:
                conn = connectors.get(a["network"])
                if a["network"] == "reminder":
                    summary["skipped"] += 1
                    continue
                now = store.now()
                try:
                    res = conn.check(account_out(a), store.creds_of(a)) or {}
                except PublishError as e:
                    if e.kind == "auth" or e.status in (401, 403):
                        store.execute("UPDATE accounts SET status='expired', error=?, check_error=?, last_check_at=? WHERE id=?",
                                      (str(e), str(e), now, a["id"]))
                        if a["status"] != "expired":
                            store.log("health", f"❌ {conn.name} · {a['name']}: {e}", "error")
                        summary["dead"] += 1
                    else:
                        store.execute("UPDATE accounts SET check_error=?, last_check_at=? WHERE id=?",
                                      (f"Couldn't check: {e}", now, a["id"]))
                        summary["warn"] += 1
                    continue
                except Exception as e:  # noqa: BLE001
                    store.execute("UPDATE accounts SET check_error=?, last_check_at=? WHERE id=?", (f"Couldn't check: {e}", now, a["id"]))
                    summary["warn"] += 1
                    continue
                warns = " · ".join(res.get("warnings") or [])
                if a["status"] == "expired":
                    store.execute("UPDATE accounts SET status='ok', error='' WHERE id=?", (a["id"],))
                    store.log("health", f"✅ {conn.name} · {a['name']} works again.", "good")
                store.execute("UPDATE accounts SET check_error=?, last_check_at=? WHERE id=?", (warns, now, a["id"]))
                if warns and warns != (a.get("check_error") or ""):
                    store.log("health", f"⚠ {conn.name} · {a['name']}: {warns}", "warn")
                summary["warn" if warns else "healthy"] += 1
                time.sleep(0.5)
            if not account_ids:
                store.set("health_last_day", datetime.now().strftime("%Y-%m-%d"))
                store.log("health", f"Daily account check: {summary['healthy']} OK, {summary['warn']} warning(s), {summary['dead']} need reconnecting.",
                          "good" if not summary["dead"] else "error")
        finally:
            self.health_busy = False
        return summary

    # --- unified inbox
    def sync_inbox(self, account_id: int | None = None, force: bool = False) -> dict:
        self.inbox_busy = True
        out = {"new": 0, "accounts": 0, "errors": []}
        try:
            now = store.now()
            every = max(3, store.get("inbox_every_min")) * 60
            rows = store.rows("SELECT * FROM accounts WHERE status='ok'" + (" AND id=?" if account_id else ""), (account_id,) if account_id else ())
            for a in rows:
                conn = connectors.REGISTRY.get(a["network"])
                if not conn or not conn.inbox:
                    continue
                if not force and ((a["inbox_retry_at"] or 0) > now or now - (a["inbox_synced_at"] or 0) < every):
                    continue
                posts = [{"target_id": r["id"], "remote_id": r["remote_id"], "text": r["ptext"] or r["text"], "url": r["url"]} for r in store.rows(
                    "SELECT t.*, p.text ptext FROM targets t JOIN posts p ON p.id=t.post_id WHERE t.account_id=? AND t.status='published'"
                    " AND t.remote_id<>'' AND t.published_at>=? ORDER BY t.published_at DESC LIMIT 25", (a["id"], now - 21 * 86400))]
                state = store.jd(a["inbox_state"])
                try:
                    items, state = conn.inbox_fetch(account_out(a), store.creds_of(a), state, posts)
                except PublishError as e:
                    wait = 86400 if e.kind == "plan" else 6 * 3600 if e.kind == "auth" else 1800
                    store.execute("UPDATE accounts SET inbox_error=?, inbox_retry_at=?, inbox_synced_at=? WHERE id=?", (str(e), now + wait, now, a["id"]))
                    out["errors"].append(f"{a['name']}: {e}")
                    continue
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    store.execute("UPDATE accounts SET inbox_error=?, inbox_retry_at=? WHERE id=?", (f"Unexpected error: {e}", now + 1800, a["id"]))
                    continue
                fresh = inbox_mod.save(a, items)
                store.execute("UPDATE accounts SET inbox_state=?, inbox_synced_at=?, inbox_error='', inbox_retry_at=NULL WHERE id=?",
                              (json.dumps(state), now, a["id"]))
                out["accounts"] += 1
                out["new"] += len(fresh)
                if fresh:
                    q = sum(1 for f in fresh if inbox_mod.tag_of(f["text"]) in ("question", "complaint"))
                    store.log("inbox", f"💬 {len(fresh)} new message(s) on {conn.name} · {a['name']}" + (f" — {q} need a reply" if q else ""), "notify")
                if not force:
                    break  # one account per round keeps every network's API calls gentle
        finally:
            self.inbox_busy = False
        return out

    # --- RSS / Atom feeds
    def check_feeds(self, force_id: int | None = None) -> None:
        now = store.now()
        q = "SELECT * FROM feeds WHERE enabled=1" if not force_id else "SELECT * FROM feeds WHERE id=?"
        for f in store.rows(q, (force_id,) if force_id else ()):
            if not force_id and f["last_checked"] and now - f["last_checked"] < f["every_min"] * 60:
                continue
            try:
                items = fetch_feed(f["url"])
                err = ""
            except Exception as e:  # noqa: BLE001
                items, err = [], str(e)[:300]
            seen = store.jl(f["seen"])
            first_run = not f["last_checked"] and not seen
            new = [it for it in items if it["guid"] not in seen]
            if not first_run:
                for it in reversed(new[:5]):
                    self._feed_post(f, it)
                if new:
                    store.log("feeds", f"RSS “{f['name']}”: {min(5, len(new))} new item(s) → posts created.", "good")
            elif items:
                store.log("feeds", f"RSS “{f['name']}” connected — watching for new items ({len(items)} existing items skipped).")
            seen = ([it["guid"] for it in items] + seen)[:500]
            store.execute("UPDATE feeds SET last_checked=?, seen=?, last_error=? WHERE id=?", (now, json.dumps(seen), err, f["id"]))

    def _feed_post(self, f: dict, it: dict) -> None:
        tpl = f["template"] or "{title}\n\n{link}"
        text = tpl.replace("{title}", it["title"]).replace("{link}", it["link"]).replace("{summary}", it["summary"][:400])
        accounts = [int(x) for x in store.jl(f["accounts"])]
        media_ids = []
        if it.get("image"):
            try:
                r = net.request("GET", it["image"], timeout=(10, 60))
                if len(r.content) < 15_000_000:
                    ext = os.path.splitext(it["image"].split("?")[0])[1][:5] or ".jpg"
                    tmp = os.path.join(tempfile.gettempdir(), f"pp-feed-{secrets.token_hex(6)}{ext}")
                    with open(tmp, "wb") as fh:
                        fh.write(r.content)
                    media_ids.append(media_mod.save_file(tmp, f"feed-image{ext}", f["brand_id"], move=True)["id"])
            except (PublishError, ValueError, OSError):
                pass
        if f["mode"] == "draft":
            status, when = "draft", None
        elif f["mode"] == "now":
            status, when = "scheduled", store.now()
        else:
            status, when = "scheduled", next_free_slot(accounts)
        pid = store.execute("INSERT INTO posts(brand_id,status,text,media,scheduled_at,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                            (f["brand_id"], status, text, json.dumps(media_ids), when, "rss", store.now(), store.now()))
        store.executemany("INSERT INTO targets(post_id,account_id) VALUES(?,?)", [(pid, a) for a in accounts])


def _text(el, *names) -> str:
    for n in names:
        for child in el:
            if child.tag.split("}")[-1] == n and (child.text or child.attrib.get("href")):
                return (child.text or child.attrib.get("href") or "").strip()
    return ""


def fetch_feed(url: str) -> list[dict]:
    r = net.request("GET", url, timeout=(10, 30))
    root = ET.fromstring(r.content)
    entries = [e for e in root.iter() if e.tag.split("}")[-1] in ("item", "entry")]
    out = []
    for e in entries[:30]:
        link = _text(e, "link")
        if not link:
            for child in e:
                if child.tag.split("}")[-1] == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        title = unescape(re.sub(r"<[^>]+>", "", _text(e, "title")))
        summary = unescape(re.sub(r"<[^>]+>", " ", _text(e, "description", "summary", "content"))).strip()
        summary = re.sub(r"\s+", " ", summary)
        image = ""
        for child in e.iter():
            tag = child.tag.split("}")[-1]
            if tag in ("enclosure", "content", "thumbnail") and child.attrib.get("url") and \
                    (child.attrib.get("type", "image").startswith("image") or tag == "thumbnail"):
                image = child.attrib["url"]
                break
        if not image:
            m = re.search(r"<img[^>]+src=[\"']([^\"']+)", _text(e, "description", "content", "encoded"))
            image = m.group(1) if m else ""
        guid = _text(e, "guid", "id") or link or title
        if title or link:
            out.append({"guid": guid, "title": title, "link": link, "summary": summary, "image": image})
    if not out and not entries:
        raise ValueError("This address is not an RSS/Atom feed.")
    return out
