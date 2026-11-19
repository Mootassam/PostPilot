"""PostPilot — local social media scheduler. Run: python server.py  (opens http://127.0.0.1:5088)"""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timedelta

import app_paths

# Installed copy runs with pythonw.exe (no console): log to a file before anything else can fail.
if sys.stdout is None or sys.stderr is None or app_paths.is_installed():
    _log_path = os.path.join(app_paths.data_dir(), "postpilot.log")
    try:
        if os.path.exists(_log_path) and os.path.getsize(_log_path) > 5 * 1024 * 1024:
            os.replace(_log_path, _log_path + ".old")
    except OSError:
        pass
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(_log_path, "a", encoding="utf-8", buffering=1)

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory  # noqa: E402

import ai  # noqa: E402
import connectors  # noqa: E402
import engine as engine_mod  # noqa: E402
import limits  # noqa: E402
import media as media_mod  # noqa: E402
import store  # noqa: E402
import tunnel  # noqa: E402
from net import PublishError  # noqa: E402

APP_VERSION = "1.2.0"
DEFAULT_PORT = 5088
FAKE = os.environ.get("PP_FAKE") == "1"

store.init()
eng = engine_mod.Engine()
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024
app.json.ensure_ascii = False
PENDING: dict[str, dict] = {}      # OAuth / multi-account connect results waiting for the user's pick
PORT = DEFAULT_PORT


# ------------------------------------------------------------------ guards & errors
@app.before_request
def _guard():
    if request.path.startswith("/m/"):
        if request.method != "GET":
            abort(405)
        return None  # one-time media links: the only thing reachable through the public tunnel
    host = (request.host or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        abort(403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin and origin.split("://")[-1] != request.host:
            abort(403)
        if request.headers.get("X-PP") != "1":
            abort(403)


@app.errorhandler(PublishError)
def _pub_err(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(ai.AIError)
def _ai_err(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(ValueError)
def _val_err(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(Exception)
def _any(e):
    code = getattr(e, "code", 500)
    if isinstance(code, int) and code < 500:
        return jsonify(error=getattr(e, "description", str(e))), code
    import traceback
    traceback.print_exc()
    return jsonify(error=f"Unexpected error: {e}"), 500


def body() -> dict:
    return request.get_json(silent=True) or {}


def ok(**kw):
    return jsonify({**kw, "ok": True})


def brand_id() -> int:
    bid = store.get("brand_id")
    if not store.one("SELECT 1 FROM brands WHERE id=?", (bid,)):
        bid = store.scalar("SELECT MIN(id) FROM brands")
        store.set("brand_id", bid)
    return bid


def redirect_uri() -> str:
    return f"http://localhost:{PORT}/oauth/callback"


# ------------------------------------------------------------------ pages & files
@app.get("/")
def index():
    return send_from_directory(app_paths.static_dir(), "index.html", max_age=0)


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(app_paths.static_dir(), name, max_age=0)


@app.get("/media/<int:mid>/file")
def media_file(mid):
    m = store.one("SELECT * FROM media WHERE id=?", (mid,))
    if not m or not os.path.exists(m["path"]):
        abort(404)
    return send_file(m["path"], mimetype=m["mime"], conditional=True, download_name=m["name"])


@app.get("/media/<int:mid>/thumb")
def media_thumb(mid):
    m = store.one("SELECT * FROM media WHERE id=?", (mid,))
    if not m:
        abort(404)
    m = media_mod.ensure_thumb(m)
    if m["thumb"] and os.path.exists(m["thumb"]):
        return send_file(m["thumb"], mimetype="image/jpeg", max_age=86400)
    if m["kind"] in ("image", "gif") and os.path.exists(m["path"]):
        return send_file(m["path"], max_age=86400)
    abort(404)  # never hand a video file to an <img>: it shows as a broken image


@app.get("/m/<tok>/<name>")
def public_media(tok, name):
    path = engine_mod.PUBLIC.get(tok)
    if not path or os.path.basename(path) != name or not os.path.exists(path):
        abort(404)
    return send_file(path, conditional=True)


@app.get("/api/ping")
def ping():
    return jsonify(app="PostPilot", version=APP_VERSION)


@app.post("/api/quit")
def quit_app():
    threading.Timer(0.6, lambda: os._exit(0)).start()
    return ok()


# ------------------------------------------------------------------ status & dashboard
@app.get("/api/status")
def status():
    bid = brand_id()
    since = int(request.args.get("since") or 0)
    notes = store.rows("SELECT * FROM activity WHERE id>? AND level IN ('good','error','notify','warn') ORDER BY id DESC LIMIT 10", (since,))
    return jsonify(
        version=APP_VERSION, fake=FAKE, brand_id=bid, theme=store.get("theme"),
        brands=store.rows("SELECT id,name,color FROM brands ORDER BY id"),
        counts={
            "scheduled": store.scalar("SELECT COUNT(*) FROM posts WHERE brand_id=? AND status='scheduled'", (bid,), 0),
            "drafts": store.scalar("SELECT COUNT(*) FROM posts WHERE brand_id=? AND status='draft'", (bid,), 0),
            "failed": store.scalar("SELECT COUNT(*) FROM posts WHERE brand_id=? AND status IN ('failed','partial') AND published_at>=?",
                                   (bid, store.now() - 7 * 86400), 0),
            "reminders": store.scalar("SELECT COUNT(*) FROM targets t JOIN posts p ON p.id=t.post_id WHERE p.brand_id=? AND t.status='reminder'", (bid,), 0),
            "accounts": store.scalar("SELECT COUNT(*) FROM accounts WHERE brand_id=?", (bid,), 0),
            "account_issues": store.scalar("SELECT COUNT(*) FROM accounts WHERE brand_id=? AND (status<>'ok' OR error<>'')", (bid,), 0),
            "publishing": store.scalar("SELECT COUNT(*) FROM targets WHERE status='publishing'", (), 0),
            "inbox_new": store.scalar("SELECT COUNT(*) FROM inbox i JOIN accounts a ON a.id=i.account_id WHERE a.brand_id=? AND i.status='new'", (bid,), 0),
            "inbox_needs": store.scalar("SELECT COUNT(*) FROM inbox i JOIN accounts a ON a.id=i.account_id WHERE a.brand_id=? AND i.status IN ('new','read')"
                                        " AND i.tag IN ('question','complaint')", (bid,), 0),
            "expiring": store.scalar("SELECT COUNT(*) FROM accounts WHERE brand_id=? AND expires_at IS NOT NULL AND expires_at < ?",
                                     (bid, store.now() + 7 * 86400), 0),
        },
        notifications=notes, last_activity=store.scalar("SELECT MAX(id) FROM activity", (), 0),
        ai=ai.provider(), tunnel=tunnel.status()["status"])


@app.get("/api/dashboard")
def dashboard():
    bid = brand_id()
    now = store.now()
    upcoming = [post_out(p) for p in store.rows("SELECT * FROM posts WHERE brand_id=? AND status='scheduled' ORDER BY scheduled_at LIMIT 8", (bid,))]
    recent = [post_out(p) for p in store.rows("SELECT * FROM posts WHERE brand_id=? AND status IN ('published','partial','failed')"
                                              " ORDER BY published_at DESC LIMIT 6", (bid,))]
    week = store.one("SELECT COUNT(*) n FROM targets t JOIN posts p ON p.id=t.post_id WHERE p.brand_id=? AND t.status='published' AND t.published_at>=?",
                     (bid, now - 7 * 86400))["n"]
    eng7 = {"likes": 0, "comments": 0, "shares": 0, "views": 0}
    for r in store.rows("SELECT t.metrics FROM targets t JOIN posts p ON p.id=t.post_id WHERE p.brand_id=? AND t.published_at>=?", (bid, now - 30 * 86400)):
        m = store.jd(r["metrics"])
        for k in eng7:
            eng7[k] += int(m.get(k) or 0)
    days = []
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(7):
        d0 = start + timedelta(days=i)
        n = store.scalar("SELECT COUNT(*) FROM posts WHERE brand_id=? AND status='scheduled' AND scheduled_at>=? AND scheduled_at<?",
                         (bid, int(d0.timestamp()), int((d0 + timedelta(days=1)).timestamp())), 0)
        days.append({"day": d0.strftime("%a"), "date": d0.strftime("%d"), "n": n})
    return jsonify(upcoming=upcoming, recent=recent, published_7d=week, engagement_30d=eng7, week=days,
                   accounts=[account_out(a) for a in store.rows("SELECT * FROM accounts WHERE brand_id=? ORDER BY network, name", (bid,))],
                   activity=store.rows("SELECT * FROM activity ORDER BY id DESC LIMIT 10"))


# ------------------------------------------------------------------ brands
@app.get("/api/brands")
def brands_list():
    return jsonify(brands=store.rows("SELECT b.*, (SELECT COUNT(*) FROM accounts a WHERE a.brand_id=b.id) accounts FROM brands b ORDER BY id"),
                   current=brand_id())


@app.post("/api/brands")
def brands_save():
    b = body()
    name = (b.get("name") or "").strip()
    if not name:
        raise ValueError("Give the brand a name.")
    color = b.get("color") or "#6d5dfc"
    if b.get("id"):
        store.execute("UPDATE brands SET name=?, color=? WHERE id=?", (name, color, int(b["id"])))
        return ok(id=int(b["id"]))
    bid = store.execute("INSERT INTO brands(name,color,created_at) VALUES(?,?,?)", (name, color, store.now()))
    store.set("brand_id", bid)
    return ok(id=bid)


@app.post("/api/brands/select")
def brands_select():
    bid = int(body().get("id") or 0)
    if not store.one("SELECT 1 FROM brands WHERE id=?", (bid,)):
        raise ValueError("Brand not found.")
    store.set("brand_id", bid)
    return ok()


@app.delete("/api/brands/<int:bid>")
def brands_delete(bid):
    if store.scalar("SELECT COUNT(*) FROM brands", (), 0) <= 1:
        raise ValueError("You need at least one brand.")
    for p in store.rows("SELECT id FROM posts WHERE brand_id=?", (bid,)):
        store.execute("DELETE FROM targets WHERE post_id=?", (p["id"],))
    for t in ("posts", "accounts", "ideas", "hashtag_groups", "templates", "feeds"):
        store.execute(f"DELETE FROM {t} WHERE brand_id=?", (bid,))
    store.execute("DELETE FROM brands WHERE id=?", (bid,))
    brand_id()
    return ok()


# ------------------------------------------------------------------ networks & accounts
def account_out(a: dict) -> dict:
    c = connectors.REGISTRY.get(a["network"])
    extra = store.jd(a["extra"])
    return {"id": a["id"], "network": a["network"], "network_name": c.name if c else a["network"], "color": c.color if c else "#888",
            "abbr": c.abbr if c else "?", "name": a["name"], "handle": a["handle"], "avatar": a["avatar"], "status": a["status"],
            "error": a["error"], "expires_at": a["expires_at"], "slots": store.jl(a["slots"]) if a["slots"] else None,
            "boards": extra.get("boards"), "board_id": extra.get("board_id"), "app": extra.get("app"),
            "posts": store.scalar("SELECT COUNT(*) FROM targets WHERE account_id=? AND status='published'", (a["id"],), 0),
            "last_check_at": a.get("last_check_at"), "check_error": a.get("check_error") or "",
            "inbox": bool(c and c.inbox), "inbox_error": a.get("inbox_error") or "", "inbox_synced_at": a.get("inbox_synced_at"),
            "auto_renew": a["network"] in ("threads", "pinterest", "youtube") and bool(a["expires_at"] is not None or a["network"] == "youtube"),
            "usage": limits.usage(a)}


@app.get("/api/networks")
def networks():
    out = []
    for c in connectors.ORDER:
        m = c.meta()
        m["guide"] = [g.replace("{redirect}", redirect_uri()) for g in m["guide"]]
        m["app_key"] = connectors.app_key(c.key)
        app_c = store.app_creds(m["app_key"])
        m["app_saved"] = {f["key"]: (bool(app_c.get(f["key"])) if f.get("secret") else app_c.get(f["key"], "")) for f in c.app_fields}
        out.append(m)
    return jsonify(networks=out, redirect=redirect_uri())


@app.get("/api/accounts")
def accounts_list():
    return jsonify(accounts=[account_out(a) for a in store.rows("SELECT * FROM accounts WHERE brand_id=? ORDER BY network, name", (brand_id(),))],
                   default_slots=store.jl(store.get("default_slots")))


def _add_accounts(found: list[dict]) -> list[int]:
    bid = brand_id()
    ids = []
    for f in found:
        ex = store.one("SELECT * FROM accounts WHERE brand_id=? AND network=? AND external_id=? AND external_id<>''",
                       (bid, f["network"], f.get("external_id", "")))
        extra = {**(store.jd(ex["extra"]) if ex else {}), **(f.get("extra") or {})}
        if ex and ex.get("extra") and store.jd(ex["extra"]).get("board_id"):
            extra["board_id"] = store.jd(ex["extra"])["board_id"]
        if ex:
            store.execute("UPDATE accounts SET name=?, handle=?, avatar=?, status='ok', error='', expires_at=?, extra=?, updated_at=? WHERE id=?",
                          (f["name"], f.get("handle", ""), f.get("avatar", ""), f.get("expires_at"), json.dumps(extra), store.now(), ex["id"]))
            aid = ex["id"]
        else:
            aid = store.execute("INSERT INTO accounts(brand_id,network,name,handle,avatar,external_id,status,expires_at,extra,created_at,updated_at)"
                                " VALUES(?,?,?,?,?,?,?,?,?,?,?)", (bid, f["network"], f["name"] or f["network"], f.get("handle", ""),
                                                                    f.get("avatar", ""), f.get("external_id", ""), "ok", f.get("expires_at"),
                                                                    json.dumps(extra), store.now(), store.now()))
        store.save_creds(aid, f["creds"])
        store.log("accounts", f"Connected {connectors.get(f['network']).name}: {f['name']}", "good")
        ids.append(aid)
    return ids


def _offer(found: list[dict]):
    """One account → add it. Several (Meta pages + Instagram…) → let the user pick."""
    if len(found) == 1:
        return ok(added=_add_accounts(found))
    tok = secrets.token_urlsafe(12)
    PENDING[tok] = {"found": found, "ts": time.time()}
    return ok(pending=tok, choices=[{"i": i, "network": f["network"], "name": f["name"], "handle": f.get("handle", ""), "avatar": f.get("avatar", ""),
                                     "exists": bool(store.one("SELECT 1 FROM accounts WHERE brand_id=? AND network=? AND external_id=?",
                                                              (brand_id(), f["network"], f.get("external_id", ""))))}
                                    for i, f in enumerate(found)])


@app.post("/api/accounts/connect")
def accounts_connect():
    b = body()
    c = connectors.get(b.get("network", ""))
    return _offer(c.connect_form(b.get("data") or {}))


@app.post("/api/accounts/pick")
def accounts_pick():
    b = body()
    p = PENDING.pop(b.get("pending", ""), None)
    if not p:
        raise ValueError("This connection expired — please connect again.")
    picks = [int(i) for i in b.get("picks") or []]
    found = [f for i, f in enumerate(p["found"]) if i in picks]
    if not found:
        raise ValueError("Pick at least one account.")
    return ok(added=_add_accounts(found))


@app.post("/api/apps/<network>")
def apps_save(network):
    c = connectors.get(network)
    key = connectors.app_key(network)
    cur = store.app_creds(key)
    for f in c.app_fields:
        v = (body().get(f["key"]) or "").strip()
        if v:
            cur[f["key"]] = v
    store.set_app_creds(key, cur)
    return ok()


@app.post("/api/oauth/start")
def oauth_start():
    network = body().get("network", "")
    c = connectors.get(network)
    state = secrets.token_urlsafe(16)
    PENDING["oauth:" + state] = {"network": network, "ts": time.time()}
    return ok(url=c.oauth_url(store.app_creds(connectors.app_key(network)), redirect_uri(), state), state=state)


@app.get("/oauth/callback")
def oauth_callback():
    state = request.args.get("state", "")
    p = PENDING.get("oauth:" + state)
    msg_ok = True
    if not p:
        msg, msg_ok = "This login link expired. Close this window and click “Log in” again.", False
    elif request.args.get("error"):
        msg, msg_ok = request.args.get("error_description") or request.args.get("error"), False
        p["result"] = {"error": msg}
    else:
        try:
            c = connectors.get(p["network"])
            found = c.oauth_finish(store.app_creds(connectors.app_key(p["network"])), redirect_uri(), request.args.get("code", ""))
            p["result"] = {"found": found}
            msg = "Connected! You can close this window and go back to PostPilot."
        except PublishError as e:
            p["result"] = {"error": str(e)}
            msg, msg_ok = str(e), False
    color = "#16a34a" if msg_ok else "#dc2626"
    html = f"""<!doctype html><meta charset=utf-8><title>PostPilot</title><body style="font-family:Segoe UI,sans-serif;display:grid;place-items:center;height:100vh;margin:0;background:#f6f7fb">
<div style="background:#fff;padding:32px 40px;border-radius:16px;box-shadow:0 10px 40px rgba(0,0,0,.08);text-align:center;max-width:460px">
<div style="font-size:42px">{'✅' if msg_ok else '⚠️'}</div><h2 style="color:{color};margin:8px 0">{'Connected' if msg_ok else 'Not connected'}</h2>
<p style="color:#475569">{msg}</p></div><script>setTimeout(()=>window.close(),{2500 if msg_ok else 15000})</script></body>"""
    return Response(html, mimetype="text/html")


@app.get("/api/oauth/result")
def oauth_result():
    state = request.args.get("state", "")
    p = PENDING.get("oauth:" + state)
    if not p:
        return jsonify(status="expired")
    r = p.get("result")
    if not r:
        return jsonify(status="waiting")
    PENDING.pop("oauth:" + state, None)
    if r.get("error"):
        return jsonify(status="error", error=r["error"])
    resp = _offer(r["found"]).get_json()
    return jsonify(status="done", **resp)


@app.post("/api/accounts/<int:aid>")
def accounts_update(aid):
    a = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
    if not a:
        abort(404)
    b = body()
    if "name" in b and b["name"].strip():
        store.execute("UPDATE accounts SET name=? WHERE id=?", (b["name"].strip()[:100], aid))
    if "slots" in b:
        slots = b["slots"]
        if slots is None:
            store.execute("UPDATE accounts SET slots='' WHERE id=?", (aid,))
        else:
            clean = []
            for s in slots:
                datetime.strptime(s["time"], "%H:%M")
                clean.append({"days": "".join(sorted({d for d in str(s.get("days", "0123456")) if d in "0123456"})) or "0123456", "time": s["time"]})
            store.execute("UPDATE accounts SET slots=? WHERE id=?", (json.dumps(sorted(clean, key=lambda x: x["time"])), aid))
    if "board_id" in b:
        extra = store.jd(a["extra"])
        extra["board_id"] = b["board_id"]
        store.execute("UPDATE accounts SET extra=? WHERE id=?", (json.dumps(extra), aid))
    if "paused" in b:
        store.execute("UPDATE accounts SET status=? WHERE id=?", ("paused" if b["paused"] else "ok", aid))
    return ok()


@app.delete("/api/accounts/<int:aid>")
def accounts_delete(aid):
    store.execute("UPDATE targets SET status='skipped', error='Account removed' WHERE account_id=? AND status='pending'", (aid,))
    store.execute("DELETE FROM accounts WHERE id=?", (aid,))
    return ok()


@app.post("/api/settings/slots")
def default_slots():
    slots = body().get("slots") or []
    for s in slots:
        datetime.strptime(s["time"], "%H:%M")
    store.set("default_slots", json.dumps(sorted(slots, key=lambda x: x["time"])))
    return ok()


# ------------------------------------------------------------------ media library
@app.get("/api/media")
def media_list():
    q = (request.args.get("q") or "").lower()
    rows = store.rows("SELECT * FROM media WHERE brand_id=? ORDER BY id DESC LIMIT 500", (brand_id(),))
    out = [media_mod.out(m) for m in rows if not q or q in m["name"].lower() or q in (m["alt"] or "").lower() or q in (m["tags"] or "").lower()]
    for m in out:
        m["used"] = store.scalar("SELECT COUNT(*) FROM posts WHERE media LIKE ?", (f"%{m['id']}%",), 0)
    return jsonify(media=out)


@app.post("/api/media")
def media_upload():
    files = request.files.getlist("files")
    if not files:
        raise ValueError("No file received.")
    out, errors = [], []
    for f in files:
        tmp = os.path.join(tempfile.gettempdir(), f"pp-up-{uuid.uuid4().hex}")
        f.save(tmp)
        try:
            out.append(media_mod.out(media_mod.save_file(tmp, f.filename or "file", brand_id(), move=True)))
        except ValueError as e:
            errors.append(str(e))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return ok(media=out, errors=errors)


@app.post("/api/media/<int:mid>")
def media_update(mid):
    b = body()
    if "alt" in b:
        store.execute("UPDATE media SET alt=? WHERE id=?", (str(b["alt"])[:1500], mid))
    if "tags" in b:
        store.execute("UPDATE media SET tags=? WHERE id=?", (json.dumps([t.strip() for t in b["tags"] if t.strip()]), mid))
    return ok()


@app.delete("/api/media/<int:mid>")
def media_delete(mid):
    if store.scalar("SELECT COUNT(*) FROM posts WHERE status='scheduled' AND (media LIKE ? OR media LIKE ? OR media LIKE ? OR media=?)",
                    (f"[{mid},%", f"%, {mid},%", f"%, {mid}]", f"[{mid}]"), 0):
        raise ValueError("This file is used by a scheduled post — remove it from the post first.")
    media_mod.delete(mid)
    return ok()


# ------------------------------------------------------------------ posts
def post_out(p: dict, full: bool = False) -> dict:
    d = dict(p)
    for k in ("media", "labels"):
        d[k] = store.jl(p[k])
    d["recurring"] = store.jd(p["recurring"])
    d["media_items"] = [media_mod.out(m) for m in (store.one("SELECT * FROM media WHERE id=?", (i,)) for i in d["media"]) if m]
    ts = store.rows("SELECT t.*, a.network, a.name account_name, a.avatar, a.handle FROM targets t LEFT JOIN accounts a ON a.id=t.account_id"
                    " WHERE t.post_id=? ORDER BY t.id", (p["id"],))
    out_t = []
    for t in ts:
        c = connectors.REGISTRY.get(t["network"] or "")
        out_t.append({"id": t["id"], "account_id": t["account_id"], "network": t["network"], "account_name": t["account_name"] or "(removed)",
                      "avatar": t["avatar"], "color": c.color if c else "#999", "abbr": c.abbr if c else "?", "status": t["status"],
                      "url": t["url"], "error": t["error"], "text": t["text"], "options": store.jd(t["options"]),
                      "metrics": store.jd(t["metrics"]), "published_at": t["published_at"], "attempts": t["attempts"], "next_try_at": t["next_try_at"]})
    d["targets"] = out_t
    return d


@app.get("/api/posts")
def posts_list():
    bid = brand_id()
    st = request.args.get("status") or ""
    sql, args = "SELECT * FROM posts WHERE brand_id=?", [bid]
    if st == "failed":
        sql += " AND status IN ('failed','partial')"
    elif st:
        sql += " AND status=?"
        args.append(st)
    f, t = request.args.get("from"), request.args.get("to")
    if f and t:
        sql += " AND COALESCE(scheduled_at, published_at, created_at) BETWEEN ? AND ?"
        args += [int(f), int(t)]
    q = (request.args.get("q") or "").strip()
    if q:
        sql += " AND text LIKE ?"
        args.append(f"%{q}%")
    acc = request.args.get("account")
    if acc:
        sql += " AND id IN (SELECT post_id FROM targets WHERE account_id=?)"
        args.append(int(acc))
    order = "scheduled_at" if st == "scheduled" else "COALESCE(published_at, scheduled_at, updated_at) DESC"
    sql += f" ORDER BY {order} LIMIT 500"
    return jsonify(posts=[post_out(p) for p in store.rows(sql, args)])


@app.get("/api/posts/<int:pid>")
def posts_get(pid):
    p = store.one("SELECT * FROM posts WHERE id=?", (pid,))
    if not p:
        abort(404)
    return jsonify(post=post_out(p))


def _targets_from(b: dict) -> list[dict]:
    out = []
    for t in b.get("targets") or []:
        aid = int(t.get("account_id") or 0)
        if not store.one("SELECT 1 FROM accounts WHERE id=? AND brand_id=?", (aid, brand_id())):
            continue
        out.append({"account_id": aid, "text": (t.get("text") or "").strip(), "options": t.get("options") or {}})
    return out


@app.post("/api/posts/validate")
def posts_validate():
    b = body()
    post = {"text": b.get("text") or "", "media": [int(x) for x in b.get("media") or []], "link": b.get("link") or ""}
    targets = _targets_from(b)
    when = {"now": store.now(), "schedule": int(b.get("scheduled_at") or store.now())}.get(b.get("mode") or "", None)
    if when is None:
        when = engine_mod.next_free_slot([t["account_id"] for t in targets]) if targets else store.now()
    warnings = {}
    for t in targets:
        a = store.one("SELECT * FROM accounts WHERE id=?", (t["account_id"],))
        w = limits.forecast(a, when, exclude_post=int(b["id"]) if b.get("id") else None) if a else []
        if w:
            warnings[str(a["id"])] = w
    return jsonify(problems=engine_mod.validate_post(post, targets), warnings=warnings)


@app.post("/api/posts")
def posts_save():
    b = body()
    action = b.get("action") or "draft"   # draft | schedule | queue | now
    targets = _targets_from(b)
    media_ids = [int(x) for x in b.get("media") or []]
    text = (b.get("text") or "").rstrip()
    if action != "draft":
        if not targets:
            raise ValueError("Choose at least one account to post to.")
        problems = engine_mod.validate_post({"text": text, "media": media_ids, "link": b.get("link") or ""}, targets)
        bad = {k: v for k, v in problems.items() if v}
        if bad:
            names = {str(a["id"]): a["name"] for a in store.rows("SELECT id,name FROM accounts")}
            raise ValueError(" · ".join(f"{names.get(k, k)}: {' '.join(v)}" for k, v in bad.items()))
    when = None
    if action == "schedule":
        when = int(b.get("scheduled_at") or 0)
        if when < store.now() - 60:
            raise ValueError("That time is in the past — pick a future time or use “Post now”.")
    elif action == "now":
        when = store.now()
    elif action == "queue":
        when = engine_mod.next_free_slot([t["account_id"] for t in targets], exclude_post=int(b["id"]) if b.get("id") else None)
    status = "draft" if action == "draft" else "scheduled"
    rec = b.get("recurring") or {}
    rec = json.dumps({"every_days": float(rec.get("every_days")), "times": int(rec.get("times") or 0), "done": 0}) \
        if rec and float(rec.get("every_days") or 0) > 0 else ""
    vals = (status, text, json.dumps(media_ids), (b.get("first_comment") or "").strip(), (b.get("link") or "").strip(),
            int(bool(b.get("utm"))), json.dumps(b.get("labels") or []), when, rec, (b.get("notes") or "")[:2000], store.now())
    if b.get("id"):
        pid = int(b["id"])
        cur = store.one("SELECT * FROM posts WHERE id=? AND brand_id=?", (pid, brand_id()))
        if not cur:
            raise ValueError("Post not found.")
        _block_if_handed(pid)
        if store.scalar("SELECT COUNT(*) FROM targets WHERE post_id=? AND status IN ('published','publishing','reminder')", (pid,), 0):
            raise ValueError("This post is already (partly) published — duplicate it to make a new version.")
        store.execute("UPDATE posts SET status=?, text=?, media=?, first_comment=?, link=?, utm=?, labels=?, scheduled_at=?, recurring=?, notes=?,"
                      " updated_at=?, published_at=NULL WHERE id=?", (*vals, pid))
        store.execute("DELETE FROM targets WHERE post_id=?", (pid,))
    else:
        pid = store.execute("INSERT INTO posts(status,text,media,first_comment,link,utm,labels,scheduled_at,recurring,notes,updated_at,brand_id,source,created_at)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*vals, brand_id(), b.get("source") or "composer", store.now()))
    store.executemany("INSERT INTO targets(post_id,account_id,text,options) VALUES(?,?,?,?)",
                      [(pid, t["account_id"], t["text"], json.dumps(t["options"])) for t in targets])
    if b.get("idea_id"):
        store.execute("DELETE FROM ideas WHERE id=?", (int(b["idea_id"]),))
    if action == "now":
        threading.Thread(target=eng.dispatch, daemon=True).start()
    return ok(post=post_out(store.one("SELECT * FROM posts WHERE id=?", (pid,))))


def _block_if_handed(pid: int) -> None:
    """A post already uploaded to a network's own scheduler can't be changed from here any more."""
    nets = [r["network"] for r in store.rows("SELECT a.network FROM targets t JOIN accounts a ON a.id=t.account_id WHERE t.post_id=? AND t.status='handed'", (pid,))]
    if nets:
        names = ", ".join(sorted({connectors.get(n).name for n in nets}))
        where = " / ".join(sorted({"YouTube Studio" if n == "youtube" else "Meta Business Suite" if n == "facebook" else connectors.get(n).name for n in nets}))
        raise ValueError(f"This post is already uploaded to {names} and goes live there by itself at its time. "
                         f"To change the time or text, edit it in {where}.")


@app.post("/api/posts/<int:pid>/action")
def posts_action(pid):
    p = store.one("SELECT * FROM posts WHERE id=? AND brand_id=?", (pid, brand_id()))
    if not p:
        abort(404)
    b = body()
    a = b.get("action")
    if a == "delete":
        if store.scalar("SELECT COUNT(*) FROM targets WHERE post_id=? AND status='handed'", (pid,), 0):
            store.log("posts", "A deleted post was already uploaded to the network's scheduler — delete it there too (e.g. YouTube Studio), "
                               "or it still goes live.", "warn")
        store.execute("DELETE FROM targets WHERE post_id=?", (pid,))
        store.execute("DELETE FROM posts WHERE id=?", (pid,))
    elif a == "duplicate":
        nid = store.execute("INSERT INTO posts(brand_id,status,text,media,first_comment,link,utm,labels,notes,source,created_at,updated_at)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (p["brand_id"], "draft", p["text"], p["media"], p["first_comment"], p["link"],
                                                                p["utm"], p["labels"], p["notes"], "duplicate", store.now(), store.now()))
        for t in store.rows("SELECT * FROM targets WHERE post_id=?", (pid,)):
            store.execute("INSERT INTO targets(post_id,account_id,text,options) VALUES(?,?,?,?)", (nid, t["account_id"], t["text"], t["options"]))
        return ok(id=nid)
    elif a == "retry":
        store.execute("UPDATE targets SET status='pending', attempts=0, next_try_at=NULL, error='' WHERE post_id=? AND status='failed'", (pid,))
        store.execute("UPDATE posts SET status='scheduled', scheduled_at=MIN(COALESCE(scheduled_at,?),?) WHERE id=?", (store.now(), store.now(), pid))
        threading.Thread(target=eng.dispatch, daemon=True).start()
    elif a == "unschedule":
        _block_if_handed(pid)
        if p["status"] != "scheduled":
            raise ValueError("Only scheduled posts can be moved back to drafts.")
        store.execute("UPDATE posts SET status='draft', scheduled_at=NULL WHERE id=?", (pid,))
    elif a == "move":
        _block_if_handed(pid)
        when = int(b.get("scheduled_at") or 0)
        if p["status"] not in ("scheduled", "draft"):
            raise ValueError("Published posts can't be moved.")
        if when < store.now() - 60:
            raise ValueError("You can't move a post into the past.")
        store.execute("UPDATE posts SET scheduled_at=?, status=CASE WHEN status='draft' AND ? THEN 'scheduled' ELSE status END WHERE id=?",
                      (when, int(bool(b.get("schedule"))), pid))
    elif a == "now":
        _block_if_handed(pid)
        if p["status"] not in ("scheduled", "draft"):
            raise ValueError("This post was already published.")
        store.execute("UPDATE posts SET status='scheduled', scheduled_at=? WHERE id=?", (store.now(), pid))
        threading.Thread(target=eng.dispatch, daemon=True).start()
    else:
        raise ValueError("Unknown action.")
    return ok()


@app.post("/api/targets/<int:tid>/done")
def target_done(tid):
    t = store.one("SELECT * FROM targets WHERE id=?", (tid,))
    if not t:
        abort(404)
    url = (body().get("url") or "").strip()
    store.execute("UPDATE targets SET status='published', url=COALESCE(NULLIF(?,''),url), published_at=? WHERE id=?", (url, store.now(), tid))
    return ok()


@app.get("/api/reminders")
def reminders():
    rows = store.rows("SELECT t.id FROM targets t JOIN posts p ON p.id=t.post_id WHERE p.brand_id=? AND t.status='reminder' ORDER BY t.published_at",
                      (brand_id(),))
    out = []
    for r in rows:
        t = store.one("SELECT t.*, a.name account_name, a.extra FROM targets t LEFT JOIN accounts a ON a.id=t.account_id WHERE t.id=?", (r["id"],))
        p = store.one("SELECT * FROM posts WHERE id=?", (t["post_id"],))
        po = post_out(p)
        out.append({"target_id": t["id"], "account": t["account_name"], "app": store.jd(t["extra"]).get("app", ""),
                    "text": (t["text"] or p["text"]) + (f"\n\n{p['link']}" if p["link"] and p["link"] not in (t["text"] or p["text"]) else ""),
                    "media": po["media_items"], "due": p["scheduled_at"]})
    return jsonify(reminders=out)


@app.get("/api/queue/next")
def queue_next():
    ids = [int(x) for x in (request.args.get("accounts") or "").split(",") if x.strip().isdigit()]
    return jsonify(ts=engine_mod.next_free_slot(ids))


@app.get("/api/best-times")
def best_times():
    nets = [n for n in (request.args.get("networks") or "").split(",") if n]
    return jsonify(best=engine_mod.best_times(nets))


# ------------------------------------------------------------------ ideas, hashtags, templates
@app.get("/api/ideas")
def ideas_list():
    return jsonify(ideas=store.rows("SELECT * FROM ideas WHERE brand_id=? ORDER BY updated_at DESC", (brand_id(),)))


@app.post("/api/ideas")
def ideas_save():
    b = body()
    title = (b.get("title") or "").strip()
    if not title:
        raise ValueError("Write the idea first.")
    stage = b.get("stage") if b.get("stage") in ("idea", "draft", "ready") else "idea"
    if b.get("id"):
        store.execute("UPDATE ideas SET title=?, body=?, stage=?, color=?, updated_at=? WHERE id=?",
                      (title, b.get("body") or "", stage, b.get("color") or "", store.now(), int(b["id"])))
    else:
        store.execute("INSERT INTO ideas(brand_id,title,body,stage,color,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (brand_id(), title, b.get("body") or "", stage, b.get("color") or "", store.now(), store.now()))
    return ok()


@app.delete("/api/ideas/<int:iid>")
def ideas_delete(iid):
    store.execute("DELETE FROM ideas WHERE id=?", (iid,))
    return ok()


def _crud(table: str, fields: tuple[str, str]):
    def list_():
        return jsonify(items=store.rows(f"SELECT * FROM {table} WHERE brand_id=? ORDER BY name COLLATE NOCASE", (brand_id(),)))

    def save():
        b = body()
        a, c = (b.get(fields[0]) or "").strip(), (b.get(fields[1]) or "").strip()
        if not a or not c:
            raise ValueError("Name and content are required.")
        if b.get("id"):
            store.execute(f"UPDATE {table} SET {fields[0]}=?, {fields[1]}=? WHERE id=?", (a, c, int(b["id"])))
        else:
            store.execute(f"INSERT INTO {table}(brand_id,{fields[0]},{fields[1]}) VALUES(?,?,?)", (brand_id(), a, c))
        return ok()

    def delete(iid):
        store.execute(f"DELETE FROM {table} WHERE id=?", (iid,))
        return ok()

    app.add_url_rule(f"/api/{table}", f"{table}_list", list_, methods=["GET"])
    app.add_url_rule(f"/api/{table}", f"{table}_save", save, methods=["POST"])
    app.add_url_rule(f"/api/{table}/<int:iid>", f"{table}_delete", delete, methods=["DELETE"])


_crud("hashtag_groups", ("name", "tags"))
_crud("templates", ("name", "text"))


# ------------------------------------------------------------------ automation: RSS feeds + CSV import
@app.get("/api/feeds")
def feeds_list():
    rows = store.rows("SELECT * FROM feeds WHERE brand_id=? ORDER BY id DESC", (brand_id(),))
    for r in rows:
        r["accounts"] = store.jl(r["accounts"])
        r.pop("seen", None)
        r["posts"] = store.scalar("SELECT COUNT(*) FROM posts WHERE source='rss' AND brand_id=?", (brand_id(),), 0)
    return jsonify(feeds=rows)


@app.post("/api/feeds")
def feeds_save():
    b = body()
    url = (b.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("Enter the feed address (https://…).")
    items = engine_mod.fetch_feed(url)
    accounts = [int(x) for x in b.get("accounts") or []]
    if not accounts:
        raise ValueError("Choose at least one account.")
    vals = ((b.get("name") or "").strip() or url, url, json.dumps(accounts), b.get("template") or "{title}\n\n{link}",
            b.get("mode") if b.get("mode") in ("queue", "draft", "now") else "queue", max(15, int(b.get("every_min") or 60)), int(bool(b.get("enabled", True))))
    if b.get("id"):
        store.execute("UPDATE feeds SET name=?, url=?, accounts=?, template=?, mode=?, every_min=?, enabled=? WHERE id=?", (*vals, int(b["id"])))
    else:
        store.execute("INSERT INTO feeds(name,url,accounts,template,mode,every_min,enabled,brand_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (*vals, brand_id(), store.now()))
    return ok(items=len(items), sample=items[:3])


@app.delete("/api/feeds/<int:fid>")
def feeds_delete(fid):
    store.execute("DELETE FROM feeds WHERE id=?", (fid,))
    return ok()


@app.post("/api/feeds/<int:fid>/check")
def feeds_check(fid):
    eng.check_feeds(force_id=fid)
    f = store.one("SELECT last_error FROM feeds WHERE id=?", (fid,))
    if f and f["last_error"]:
        raise ValueError(f["last_error"])
    return ok()


@app.get("/api/import/template")
def import_template():
    nets = ",".join(sorted({a["network"] for a in store.rows("SELECT network FROM accounts WHERE brand_id=?", (brand_id(),))})) or "facebook,linkedin"
    when = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0).strftime("%Y-%m-%d %H:%M")
    data = "﻿text,date,accounts,media,link\n" + f'"Hello world! 👋 Our first scheduled post.",{when},"{nets}",,https://example.com\n' \
        + '"Tip: leave the date empty to add a post to the queue.",,all,,\n'
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=postpilot-import-template.csv"})


@app.post("/api/import/csv")
def import_csv():
    f = request.files.get("file")
    if not f:
        raise ValueError("Choose a CSV file.")
    text = f.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    accounts = store.rows("SELECT * FROM accounts WHERE brand_id=?", (brand_id(),))
    created, errors = 0, []
    for i, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        body_text = row.get("text") or row.get("message") or row.get("caption") or ""
        if not body_text:
            continue
        want = [w.strip().lower() for w in (row.get("accounts") or "all").replace(";", ",").split(",") if w.strip()]
        ids = [a["id"] for a in accounts if "all" in want or a["network"] in want or a["name"].lower() in want or a["handle"].lower().lstrip("@") in
               [w.lstrip("@") for w in want]]
        if not ids:
            errors.append(f"Row {i}: no matching account for “{row.get('accounts')}”.")
            continue
        media_ids = []
        for src in [s.strip() for s in (row.get("media") or "").split("|") if s.strip()]:
            try:
                if src.startswith(("http://", "https://")):
                    import net
                    r = net.request("GET", src, timeout=(10, 120))
                    tmp = os.path.join(tempfile.gettempdir(), f"pp-csv-{uuid.uuid4().hex}{os.path.splitext(src.split('?')[0])[1][:5]}")
                    with open(tmp, "wb") as fh:
                        fh.write(r.content)
                    media_ids.append(media_mod.save_file(tmp, os.path.basename(src.split("?")[0]) or "image.jpg", brand_id(), move=True)["id"])
                elif os.path.isfile(src):
                    media_ids.append(media_mod.save_file(src, os.path.basename(src), brand_id())["id"])
                else:
                    errors.append(f"Row {i}: media not found: {src}")
            except (PublishError, ValueError, OSError) as e:
                errors.append(f"Row {i}: {e}")
        when = None
        if row.get("date"):
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M", "%Y-%m-%d"):
                try:
                    when = int(datetime.strptime(row["date"], fmt).timestamp())
                    break
                except ValueError:
                    continue
            if when is None:
                errors.append(f"Row {i}: date “{row['date']}” not understood (use YYYY-MM-DD HH:MM).")
                continue
        if when is None or when < store.now():
            when = engine_mod.next_free_slot(ids)
        pid = store.execute("INSERT INTO posts(brand_id,status,text,media,link,scheduled_at,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                            (brand_id(), "scheduled", body_text, json.dumps(media_ids), row.get("link", ""), when, "csv", store.now(), store.now()))
        store.executemany("INSERT INTO targets(post_id,account_id) VALUES(?,?)", [(pid, a) for a in ids])
        created += 1
    store.log("import", f"CSV import: {created} post(s) scheduled" + (f", {len(errors)} problem(s)" if errors else ""), "good" if created else "warn")
    return ok(created=created, errors=errors[:50])


# ------------------------------------------------------------------ analytics
@app.get("/api/analytics")
def analytics():
    bid = brand_id()
    days = max(7, min(365, int(request.args.get("days") or 30)))
    since = store.now() - days * 86400
    rows = store.rows("SELECT t.*, a.network, a.name account_name, p.text post_text, p.media post_media FROM targets t JOIN posts p ON p.id=t.post_id"
                      " LEFT JOIN accounts a ON a.id=t.account_id WHERE p.brand_id=? AND t.status='published' AND t.published_at>=?", (bid, since))
    by_net: dict[str, dict] = {}
    series: dict[str, dict] = {}
    heat = [[0, 0] for _ in range(168)]
    top = []
    for r in rows:
        m = store.jd(r["metrics"])
        eng = int(m.get("likes") or 0) + int(m.get("comments") or 0) + int(m.get("shares") or 0)
        n = by_net.setdefault(r["network"] or "?", {"posts": 0, "likes": 0, "comments": 0, "shares": 0, "views": 0, "network": r["network"]})
        n["posts"] += 1
        for k in ("likes", "comments", "shares", "views"):
            n[k] += int(m.get(k) or 0)
        day = datetime.fromtimestamp(r["published_at"]).strftime("%Y-%m-%d")
        s = series.setdefault(day, {"posts": 0, "engagement": 0})
        s["posts"] += 1
        s["engagement"] += eng
        dt = datetime.fromtimestamp(r["published_at"])
        heat[dt.weekday() * 24 + dt.hour][0] += eng
        heat[dt.weekday() * 24 + dt.hour][1] += 1
        top.append({"target_id": r["id"], "post_id": r["post_id"], "network": r["network"], "account": r["account_name"],
                    "text": ((r["text"] or r["post_text"]) or "")[:140],
                    "url": r["url"], "published_at": r["published_at"], "metrics": m, "engagement": eng,
                    "media": (lambda ids: (lambda mm: media_mod.out(mm) if mm else None)(
                        store.one("SELECT * FROM media WHERE id=?", (ids[0],)) if ids else None))(store.jl(r["post_media"]))})
    top.sort(key=lambda x: (x["engagement"], int(x["metrics"].get("views") or 0)), reverse=True)
    out_series = []
    for i in range(days, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        out_series.append({"day": d[5:], **series.get(d, {"posts": 0, "engagement": 0})})
    for n in by_net.values():
        c = connectors.REGISTRY.get(n["network"] or "")
        n["name"], n["color"] = (c.name, c.color) if c else (n["network"], "#888")
        n["eng_per_post"] = round((n["likes"] + n["comments"] + n["shares"]) / n["posts"], 1) if n["posts"] else 0
    return jsonify(networks=sorted(by_net.values(), key=lambda x: -x["posts"]), series=out_series, top=top[:10],
                   heat=[round(e / c, 1) if c else 0 for e, c in heat], total_posts=len(rows),
                   totals={k: sum(n[k] for n in by_net.values()) for k in ("likes", "comments", "shares", "views")})


@app.post("/api/analytics/refresh")
def analytics_refresh():
    store.execute("UPDATE targets SET metrics_at=NULL WHERE status='published' AND published_at>=?", (store.now() - 30 * 86400,))
    n = eng.refresh_metrics(limit=60, min_age=0)
    return ok(updated=n)


# ------------------------------------------------------------------ AI
@app.post("/api/ai")
def ai_run():
    b = body()
    return ok(**ai.run(b.get("task", ""), b.get("text", ""), b.get("network", ""), b.get("tone", ""), b.get("lang", ""), b.get("extra", "")))


# ------------------------------------------------------------------ unified inbox
def _inbox_out(r: dict) -> dict:
    c = connectors.REGISTRY.get(r["network"])
    d = dict(r)
    d.update(network_name=c.name if c else r["network"], color=c.color if c else "#888", abbr=c.abbr if c else "?")
    return d


@app.get("/api/inbox")
def inbox_list():
    bid = brand_id()
    st = request.args.get("status") or "needs"
    sql = ("SELECT i.*, a.name account_name, a.avatar account_avatar FROM inbox i JOIN accounts a ON a.id=i.account_id"
           " WHERE a.brand_id=? AND i.status<>'mine'")
    args: list = [bid]
    if st == "needs":
        sql += " AND i.status IN ('new','read')"
    elif st in ("new", "replied", "archived"):
        sql += " AND i.status=?"
        args.append(st)
    else:
        sql += " AND i.status<>'archived'"
    if request.args.get("account"):
        sql += " AND i.account_id=?"
        args.append(int(request.args["account"]))
    q = (request.args.get("q") or "").strip()
    if q:
        sql += " AND (i.text LIKE ? OR i.author_name LIKE ? OR i.author_handle LIKE ?)"
        args += [f"%{q}%"] * 3
    sql += (" ORDER BY CASE i.tag WHEN 'complaint' THEN 0 WHEN 'question' THEN 1 ELSE 2 END, i.created_at DESC LIMIT 500" if st == "needs"
            else " ORDER BY i.created_at DESC LIMIT 500")
    base = "FROM inbox i JOIN accounts a ON a.id=i.account_id WHERE a.brand_id=?"
    counts = {"needs": store.scalar(f"SELECT COUNT(*) {base} AND i.status IN ('new','read')", (bid,), 0),
              "new": store.scalar(f"SELECT COUNT(*) {base} AND i.status='new'", (bid,), 0),
              "replied": store.scalar(f"SELECT COUNT(*) {base} AND i.status='replied'", (bid,), 0),
              "archived": store.scalar(f"SELECT COUNT(*) {base} AND i.status='archived'", (bid,), 0)}
    sources = []
    for a in store.rows("SELECT * FROM accounts WHERE brand_id=? ORDER BY network, name", (bid,)):
        c = connectors.REGISTRY.get(a["network"])
        if not c or a["network"] == "reminder":
            continue
        sources.append({"id": a["id"], "name": a["name"], "network": a["network"], "network_name": c.name, "color": c.color, "abbr": c.abbr,
                        "supported": bool(c.inbox), "note": c.inbox_note, "error": a["inbox_error"] or "", "synced_at": a["inbox_synced_at"]})
    return jsonify(items=[_inbox_out(r) for r in store.rows(sql, args)], counts=counts, sources=sources)


@app.get("/api/inbox/<int:iid>")
def inbox_get(iid):
    it = store.one("SELECT i.*, a.name account_name, a.avatar account_avatar FROM inbox i JOIN accounts a ON a.id=i.account_id WHERE i.id=?", (iid,))
    if not it:
        abort(404)
    if it["status"] == "new":
        store.execute("UPDATE inbox SET status='read' WHERE id=?", (iid,))
        it["status"] = "read"
    thread = []
    if it["thread_key"]:
        thread = store.rows("SELECT * FROM inbox WHERE account_id=? AND thread_key=? ORDER BY created_at LIMIT 100", (it["account_id"], it["thread_key"]))
    return jsonify(item=_inbox_out(it), thread=[_inbox_out(t) for t in thread])


@app.post("/api/inbox/<int:iid>")
def inbox_update(iid):
    st = body().get("status")
    if st not in ("new", "read", "archived", "replied"):
        raise ValueError("Unknown status.")
    store.execute("UPDATE inbox SET status=? WHERE id=?", (st, iid))
    return ok()


@app.post("/api/inbox/bulk")
def inbox_bulk():
    b = body()
    ids = [int(x) for x in b.get("ids") or []]
    st = b.get("status")
    if st not in ("read", "archived", "new") or not ids:
        raise ValueError("Nothing to update.")
    store.execute(f"UPDATE inbox SET status=? WHERE id IN ({','.join('?' * len(ids))})", (st, *ids))
    return ok(updated=len(ids))


@app.post("/api/inbox/<int:iid>/reply")
def inbox_reply(iid):
    text = (body().get("text") or "").strip()
    if not text:
        raise ValueError("Write your reply first.")
    it = store.one("SELECT * FROM inbox WHERE id=?", (iid,))
    a = store.one("SELECT * FROM accounts WHERE id=?", (it["account_id"],)) if it else None
    if not it or not a:
        abort(404)
    conn = connectors.get(a["network"])
    res = conn.inbox_reply(engine_mod.account_out(a), store.creds_of(a), it, text)
    store.execute("UPDATE inbox SET status='replied', reply_text=?, replied_at=? WHERE id=?", (text, store.now(), iid))
    store.log("inbox", f"Replied to {it['author_name']} on {conn.name}.", "info")
    return ok(remote_id=res.get("remote_id", ""))


@app.post("/api/inbox/sync")
def inbox_sync():
    aid = body().get("account_id")
    r = eng.sync_inbox(account_id=int(aid) if aid else None, force=True)
    return ok(**r)


# ------------------------------------------------------------------ AI content calendar
@app.post("/api/plan/generate")
def plan_generate():
    b = body()
    accounts = [store.one("SELECT * FROM accounts WHERE id=? AND brand_id=?", (int(i), brand_id())) for i in b.get("accounts") or []]
    accounts = [a for a in accounts if a]
    nets = sorted({a["network"] for a in accounts})
    brief = {**b, "networks": [connectors.get(n).name for n in nets]}
    res = ai.plan(brief)
    hours = []
    for bt in engine_mod.best_times(nets or ["facebook"]):
        hours += [h for h in bt["hours"] if h not in hours]
    hours = hours or [10, 13, 18]
    now = store.now()
    for i, it in enumerate(res["items"]):
        d = datetime.fromtimestamp(it["date"]).replace(hour=hours[i % len(hours)], minute=0)
        it["ts"] = max(int(d.timestamp()), now + 1800 + i * 600)  # never in the past; today's leftovers spread out
    title = (b.get("business") or "Plan")[:60]
    pid = store.execute("INSERT INTO plans(brand_id,title,brief,items,created_at) VALUES(?,?,?,?,?)",
                        (brand_id(), title, json.dumps(b, ensure_ascii=False), json.dumps(res["items"], ensure_ascii=False), now))
    store.log("ai", f"AI content plan created: {len(res['items'])} posts ({res['provider']}).", "good")
    return ok(id=pid, items=res["items"], provider=res["provider"])


@app.get("/api/plans")
def plans_list():
    rows = store.rows("SELECT id,title,created_at,items,brief FROM plans WHERE brand_id=? ORDER BY id DESC LIMIT 10", (brand_id(),))
    return jsonify(plans=[{"id": r["id"], "title": r["title"], "created_at": r["created_at"], "items": store.jl(r["items"]),
                           "brief": store.jd(r["brief"])} for r in rows])


@app.post("/api/plan/apply")
def plan_apply():
    b = body()
    mode = b.get("mode")
    items = b.get("items") or []
    if not items:
        raise ValueError("Select at least one post.")
    if mode == "ideas":
        for it in items:
            store.execute("INSERT INTO ideas(brand_id,title,body,stage,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                          (brand_id(), (it.get("topic") or it.get("caption", "")[:60])[:200],
                           f"{it.get('caption', '')}\n\n{it.get('hashtags', '')}\n\n📷 {it.get('media_idea', '')}".strip(), "idea", store.now(), store.now()))
        return ok(created=len(items))
    accounts = [int(a) for a in b.get("accounts") or [] if store.one("SELECT 1 FROM accounts WHERE id=? AND brand_id=?", (int(a), brand_id()))]
    if mode == "schedule" and not accounts:
        raise ValueError("Choose the accounts to post to.")
    created = 0
    for it in items:
        text = it.get("caption", "").strip()
        if b.get("hashtags", True) and it.get("hashtags"):
            text = f"{text}\n\n{it['hashtags']}"
        ts = int(it.get("ts") or 0)
        status = "scheduled" if mode == "schedule" else "draft"
        if status == "scheduled" and ts < store.now() + 60:
            raise ValueError(f"“{(it.get('topic') or text)[:40]}” is scheduled in the past — change its time.")
        notes = f"📷 {it.get('media_idea')}" if it.get("media_idea") else ""
        pid = store.execute("INSERT INTO posts(brand_id,status,text,scheduled_at,source,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                            (brand_id(), status, text, ts if status == "scheduled" else ts or None, "ai-plan", notes, store.now(), store.now()))
        store.executemany("INSERT INTO targets(post_id,account_id) VALUES(?,?)", [(pid, a) for a in accounts])
        created += 1
    store.log("ai", f"AI plan applied: {created} post(s) {'scheduled' if mode == 'schedule' else 'saved as drafts'}.", "good")
    return ok(created=created)


# ------------------------------------------------------------------ health, logins, limits
@app.post("/api/accounts/check")
def accounts_check():
    ids = [int(x) for x in body().get("ids") or []]
    if eng.health_busy:
        raise ValueError("A check is already running — try again in a moment.")
    return ok(**eng.check_health(ids or None))


@app.get("/api/logins")
def logins():
    now = store.now()
    out = []
    for a in store.rows("SELECT * FROM accounts WHERE brand_id=? ORDER BY COALESCE(expires_at, 9999999999)", (brand_id(),)):
        c = connectors.REGISTRY.get(a["network"])
        exp = a["expires_at"]
        out.append({"id": a["id"], "name": a["name"], "network": a["network"], "network_name": c.name if c else a["network"],
                    "color": c.color if c else "#888", "abbr": c.abbr if c else "?", "status": a["status"], "expires_at": exp,
                    "days_left": None if exp is None else (exp - now) // 86400,
                    "auto_renew": a["network"] in ("threads", "pinterest") and exp is not None or a["network"] == "youtube",
                    "last_check_at": a["last_check_at"], "check_error": a["check_error"] or "", "error": a["error"] or ""})
    return jsonify(logins=out)


@app.get("/api/limits")
def limits_get():
    nets = {}
    for c in connectors.ORDER:
        nets[c.key] = [{"limit": n, "window": w, "label": lbl} for n, w, lbl in limits.rules(c.key)]
    accs = [{"id": a["id"], "name": a["name"], "network": a["network"], "usage": limits.usage(a),
             "waiting": store.scalar("SELECT COUNT(*) FROM targets WHERE account_id=? AND status='pending' AND limit_note<>''", (a["id"],), 0)}
            for a in store.rows("SELECT * FROM accounts WHERE brand_id=?", (brand_id(),))]
    return jsonify(networks=nets, accounts=accs, x_plan=store.get("x_plan"), overrides=store.jd(store.get("limits")),
                   defaults={k: [{"limit": n, "window": w} for n, w, _ in v] for k, v in limits.DEFAULTS.items()})


@app.post("/api/limits")
def limits_save():
    b = body()
    if b.get("x_plan") in limits.X_PLANS:
        store.set("x_plan", b["x_plan"])
    if "overrides" in b:
        clean = {}
        for net_key, rows in (b.get("overrides") or {}).items():
            if net_key not in connectors.REGISTRY:
                continue
            ok_rows = []
            for r in rows or []:
                n, w = int(r[0]), int(r[1])
                if n < 1 or w < 60:
                    raise ValueError("Limits must be at least 1 post per minute window.")
                ok_rows.append([n, w])
            if ok_rows:
                clean[net_key] = ok_rows
        store.set("limits", json.dumps(clean))
    return ok()


# ------------------------------------------------------------------ settings
SETTINGS_PUBLIC = ["theme", "ai_provider", "ollama_model", "media_host", "public_base", "utm_source", "utm_medium", "utm_campaign", "retry_max", "notify",
                   "health_hour", "inbox_every_min", "x_plan", "native_schedule"]


@app.get("/api/settings")
def settings_get():
    s = {k: store.get(k) for k in SETTINGS_PUBLIC}
    s.update(anthropic_key_set=bool(store.get_secret("anthropic_key")), ai_active=ai.provider(), ollama=ai.ollama_ok(),
             data_dir=app_paths.data_dir(), version=APP_VERSION, default_slots=store.jl(store.get("default_slots")),
             tunnel=tunnel.status(), redirect=redirect_uri())
    return jsonify(s)


@app.post("/api/settings")
def settings_save():
    b = body()
    for k, v in b.items():
        if k == "anthropic_key":
            v = (v or "").strip()
            if v and not v.startswith("sk-"):
                raise ValueError("An Anthropic API key starts with “sk-ant-”.")
            store.set_secret("anthropic_key", v)
        elif k == "ai_provider" and v in ("auto", "anthropic", "ollama", "off"):
            store.set(k, v)
        elif k == "media_host" and v in ("auto", "custom", "off"):
            store.set(k, v)
        elif k == "public_base":
            v = (v or "").strip().rstrip("/")
            if v and not v.startswith("https://"):
                raise ValueError("The public address must start with https://")
            store.set(k, v)
        elif k == "retry_max":
            store.set(k, max(1, min(8, int(v))))
        elif k == "health_hour":
            store.set(k, max(0, min(23, int(v))))
        elif k == "inbox_every_min":
            store.set(k, max(3, min(240, int(v))))
        elif k == "native_schedule":
            store.set(k, 1 if v else 0)
        elif k in ("theme", "ollama_model", "utm_source", "utm_medium", "utm_campaign", "notify"):
            store.set(k, v)
    return ok()


@app.get("/api/activity")
def activity():
    lvl = request.args.get("level") or ""
    sql, args = "SELECT * FROM activity", []
    if lvl:
        sql += " WHERE level=?"
        args.append(lvl)
    sql += " ORDER BY id DESC LIMIT 400"
    return jsonify(items=store.rows(sql, args))


@app.get("/api/backup")
def backup():
    tmp = os.path.join(tempfile.gettempdir(), f"pp-backup-{uuid.uuid4().hex}.db")
    src, dst = sqlite3.connect(store.DB_PATH), sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    data = open(tmp, "rb").read()
    os.remove(tmp)
    return Response(data, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename=PostPilot-backup-{datetime.now():%Y%m%d-%H%M}.db"})


@app.post("/api/restore")
def restore():
    f = request.files.get("file")
    if not f:
        raise ValueError("Choose a backup file.")
    tmp = os.path.join(tempfile.gettempdir(), f"pp-restore-{uuid.uuid4().hex}.db")
    f.save(tmp)
    try:
        src = sqlite3.connect(tmp)
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"posts", "accounts", "targets", "settings"} <= tables:
            src.close()
            raise ValueError("This is not a PostPilot backup file.")
        live = sqlite3.connect(store.DB_PATH)
        with sqlite3.connect(os.path.join(app_paths.data_dir(), f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db")) as safe:
            live.backup(safe)
        with store._lock:
            src.backup(live)
        src.close()
        live.close()
        store.init()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    store.log("backup", "Backup restored.", "good")
    return ok()


# ------------------------------------------------------------------ run
PORT_FILE = os.path.join(app_paths.data_dir(), "port.txt")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _already_running(port: int) -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.loads(r.read()).get("app") == "PostPilot"
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    global PORT
    port = int(os.environ.get("PP_PORT") or DEFAULT_PORT)
    candidates = [port]
    try:
        last = int(open(PORT_FILE).read().strip())
        if last != port:
            candidates.append(last)
    except (OSError, ValueError):
        pass
    for p in candidates:
        if not _port_free(p) and _already_running(p):
            webbrowser.open(f"http://localhost:{p}")
            return
    if not _port_free(port):
        for p in range(port + 1, port + 50):
            if _port_free(p):
                port = p
                break
    PORT = engine_mod.PORT = port
    try:
        with open(PORT_FILE, "w") as f:
            f.write(str(port))
    except OSError:
        pass
    if app_paths.is_installed():
        import logging
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
    eng.start()
    threading.Thread(target=media_mod.backfill_thumbs, daemon=True).start()  # posters for videos added before 1.x
    url = f"http://localhost:{port}"
    print(f"PostPilot {APP_VERSION} running at {url}" + ("  [DEMO MODE]" if FAKE else ""))
    if os.environ.get("PP_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    from werkzeug.serving import make_server
    make_server("127.0.0.1", port, app, threaded=True).serve_forever()


def _fatal(exc: BaseException) -> None:
    import traceback
    traceback.print_exc()
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f"PostPilot could not start:\n\n{exc}\n\nDetails: "
                                         f"{os.path.join(app_paths.data_dir(), 'postpilot.log')}", "PostPilot", 0x10)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        _fatal(e)
        sys.exit(1)
