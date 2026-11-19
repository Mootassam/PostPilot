"""v1.1: unified inbox, AI content calendar, account health, login expiry, rate-limit guard."""

import json
import time

import pytest

import engine as engine_mod
import limits
import server
import store
from connectors.meta import Facebook, Instagram
from connectors.more import Demo, YouTube
from connectors.simple import Bluesky, Mastodon, Telegram
from connectors.threads import Threads
from connectors.x import X
from net import PublishError
from test_app import H, c, clean, connect, run_engine  # noqa: F401 - fixtures
from test_connectors import ACC, img, post  # noqa: F401

XC = {"api_key": "a", "api_secret": "b", "access_token": "c", "access_secret": "d"}
POSTS = [{"target_id": 5, "remote_id": "P1", "text": "Our new latte", "url": "https://x/p1"}]


# ------------------------------------------------------------------ connectors: inbox shapes
def test_facebook_inbox_and_reply(fake):
    fake.on("GET", "/P1/comments", body={"data": [
        {"id": "c1", "message": "How much?", "created_time": "2026-10-01T10:00:00+0000", "from": {"id": "u9", "name": "Sara"}, "permalink_url": "u"},
        {"id": "c2", "message": "Thanks!", "created_time": "2026-10-01T11:00:00+0000", "from": {"id": "123", "name": "Page"}, "parent": {"id": "c1"}}]})
    items, _ = Facebook().inbox_fetch(ACC, {"token": "T"}, {}, POSTS)
    assert items[0]["author_name"] == "Sara" and items[0]["kind"] == "comment" and not items[0]["mine"]
    assert items[1]["kind"] == "reply" and items[1]["mine"] and items[0]["post_target_id"] == 5
    fake.on("POST", "/c1/comments", body={"id": "r1"})
    assert Facebook().inbox_reply(ACC, {"token": "T"}, {"reply_to": "c1"}, "It's 25 MAD")["remote_id"] == "r1"


def test_instagram_quota_precheck(fake):
    fake.on("GET", "content_publishing_limit", body={"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]})
    with pytest.raises(PublishError) as e:
        Instagram().precheck(ACC, {"token": "T"})
    assert e.value.kind == "rate" and e.value.retry_after == 3600


def test_instagram_inbox_with_replies(fake):
    fake.on("GET", "/P1/comments", body={"data": [{"id": "c1", "text": "Love it", "username": "sara", "timestamp": "2026-10-01T10:00:00+0000",
                                                   "replies": {"data": [{"id": "r1", "text": "Thanks Sara", "username": "cafe", "timestamp": "2026-10-01T10:05:00+0000"}]}}]})
    items, _ = Instagram().inbox_fetch({**ACC, "extra_d": {"username": "cafe"}}, {"token": "T"}, {}, POSTS)
    assert [i["kind"] for i in items] == ["comment", "reply"] and items[1]["mine"] and items[1]["reply_to"] == "c1"


def test_x_mentions_need_paid_plan(fake):
    fake.on("GET", "/mentions", status=403, body={"title": "Client Forbidden", "reason": "client-not-enrolled"})
    with pytest.raises(PublishError) as e:
        X().inbox_fetch(ACC, XC, {}, POSTS)
    assert e.value.kind == "plan"


def test_x_rate_headers_and_partial_thread(fake):
    calls = iter([(201, {"data": {"id": "T1"}}), (429, {"title": "Too Many Requests"})])

    def resp(kw):
        return next(calls)[1]
    fake.rules.append({"m": "POST", "c": "/2/tweets", "s": 201, "b": {"data": {"id": "T1"}},
                       "h": {"x-user-limit-24hour-remaining": "3", "x-user-limit-24hour-reset": "1900000000"}, "n": 1})
    fake.on("POST", "/2/tweets", status=429, body={"title": "Too Many Requests"})
    long = " ".join(f"Point {i} matters a lot." for i in range(30))
    r = X().publish(ACC, XC, post(long))
    assert r["remote_id"] == "T1" and r["units"] == 1 and "Only 1 of" in r["warning"] and r["rate"]["remaining"] == 3


def test_telegram_updates_dm_and_comment(fake):
    fake.on("POST", "getUpdates", body={"ok": True, "result": [
        {"update_id": 10, "message": {"message_id": 1, "date": 1790000000, "chat": {"id": 77, "type": "private"}, "from": {"id": 77, "first_name": "Ali"}, "text": "Hi, price?"}},
        {"update_id": 11, "message": {"message_id": 5, "date": 1790000100, "chat": {"id": -200, "type": "supergroup"}, "from": {"id": 78, "first_name": "Mona"},
                                      "text": "Nice!", "reply_to_message": {"message_id": 4, "is_automatic_forward": True, "sender_chat": {"id": -1001}}}},
        {"update_id": 12, "message": {"message_id": 6, "date": 1790000200, "chat": {"id": -200, "type": "supergroup"}, "from": {"id": 79, "first_name": "X"}, "text": "random chat"}}]})
    items, st = Telegram().inbox_fetch({**ACC, "external_id": "-1001"}, {"bot_token": "B"}, {}, POSTS)
    assert [i["kind"] for i in items] == ["dm", "comment"] and st["offset"] == 13
    fake.on("POST", "sendMessage", body={"ok": True, "result": {"message_id": 9}})
    Telegram().inbox_reply(ACC, {"bot_token": "B"}, {"reply_to": items[0]["reply_to"]}, "Hello!")
    sent = fake.find("sendMessage")[0]["data"]
    assert sent["chat_id"] == 77 and json.loads(sent["reply_parameters"])["message_id"] == 1


def test_bluesky_notifications_and_threaded_reply(fake):
    fake.on("POST", "createSession", body={"accessJwt": "J", "did": "did:me", "handle": "me.bsky.social"})
    fake.on("GET", "listNotifications", body={"notifications": [
        {"reason": "reply", "uri": "at://did:x/app.bsky.feed.post/aaa", "cid": "c1", "indexedAt": "2026-10-01T10:00:00.000Z",
         "author": {"handle": "bob.bsky.social", "displayName": "Bob"}, "record": {"text": "Great!", "reply": {"root": {"uri": "P1", "cid": "rc"}, "parent": {"uri": "P1", "cid": "rc"}}}},
        {"reason": "like", "uri": "at://x/like/1", "cid": "z", "author": {}, "record": {}}]})
    items, _ = Bluesky().inbox_fetch(ACC, {"handle": "me", "password": "p"}, {}, POSTS)
    assert len(items) == 1 and items[0]["post_target_id"] == 5
    fake.on("POST", "createRecord", body={"uri": "at://did:me/app.bsky.feed.post/r", "cid": "x"})
    Bluesky().inbox_reply(ACC, {"handle": "me", "password": "p"}, {"reply_to": items[0]["reply_to"]}, "Thanks Bob")
    rec = fake.find("createRecord")[0]["json"]["record"]
    assert rec["reply"]["root"]["uri"] == "P1" and rec["reply"]["parent"]["uri"] == "at://did:x/app.bsky.feed.post/aaa"


def test_mastodon_dm_keeps_privacy(fake):
    fake.on("GET", "/api/v1/notifications", body=[{"id": "n9", "status": {"id": "s1", "content": "<p>@me hi in private</p>", "visibility": "direct",
                                                                          "url": "u", "created_at": "2026-10-01T10:00:00.000Z"},
                                                   "account": {"acct": "ana@mstdn.social", "display_name": "Ana"}}])
    items, st = Mastodon().inbox_fetch(ACC, {"instance": "m.social", "token": "T"}, {}, POSTS)
    assert items[0]["kind"] == "dm" and items[0]["text"] == "@me hi in private" and st["since"] == "n9"
    fake.on("POST", "/api/v1/statuses", body={"id": "s2"})
    Mastodon().inbox_reply(ACC, {"instance": "m.social", "token": "T"}, {"reply_to": items[0]["reply_to"]}, "hello")
    body = fake.find("/api/v1/statuses")[0]["json"]
    assert body["visibility"] == "direct" and body["status"].startswith("@ana@mstdn.social")


def test_youtube_comments(fake):
    fake.on("GET", "commentThreads", body={"items": [{"snippet": {"videoId": "P1", "topLevelComment": {"id": "t1", "snippet": {
        "textOriginal": "Cool video", "authorDisplayName": "Max", "authorChannelId": {"value": "UCx"}, "publishedAt": "2026-10-01T10:00:00Z"}}},
        "replies": {"comments": [{"id": "t1.r", "snippet": {"textOriginal": "Thanks", "authorChannelId": {"value": "123"}, "publishedAt": "2026-10-01T11:00:00Z"}}]}}]})
    items, _ = YouTube().inbox_fetch(ACC, {"token": "T", "token_exp": 9999999999}, {}, POSTS)
    assert items[0]["reply_to"] == "t1" and items[1]["mine"] and items[0]["post_target_id"] == 5


def test_threads_partial_thread_reports_instead_of_reposting(fake):
    n = iter(range(100))
    fake.on("POST", "/threads_publish", body=lambda kw: {"id": f"pub{next(n)}"}, times=1)
    fake.on("POST", "/threads_publish", status=500, body={"error": {"message": "down"}})
    fake.on("POST", "/123/threads", body={"id": "c"}).on("GET", "/pub0", body={"permalink": "p"})
    long = " ".join(f"Line {i} of my long story." for i in range(40))
    r = Threads().publish(ACC, {"token": "T"}, post(long))
    assert r["remote_id"] == "pub0" and "Only 1 of" in r["warning"]


# ------------------------------------------------------------------ engine: rate-limit guard
def test_guard_defers_without_counting_attempts(c):
    a = connect(c)
    store.set("limits", json.dumps({"demo": [[2, 86400]]}))
    try:
        for i in range(3):
            c.post("/api/posts", json={"text": f"post {i}", "targets": [{"account_id": a}], "action": "now"}, headers=H)
            run_engine()
        waiting = store.one("SELECT * FROM targets WHERE status='pending'")
        assert waiting and waiting["attempts"] == 0 and waiting["next_try_at"] > store.now() + 80000 and "2 posts per 24 hours" in waiting["error"]
        assert store.scalar("SELECT COUNT(*) FROM targets WHERE status='published'") == 2
        acc = store.one("SELECT * FROM accounts WHERE id=?", (a,))
        assert limits.usage(acc)[0]["used"] == 2
        r = c.post("/api/posts/validate", json={"text": "x", "targets": [{"account_id": a}], "mode": "now"}, headers=H).get_json()
        assert r["warnings"][str(a)]
    finally:
        store.set("limits", "{}")


def test_network_reported_limit_respected(c):
    a = connect(c)
    limits.remember(a, {"remaining": 0, "reset": store.now() + 3600, "label": "X says your 24-hour posting limit is used up"})
    c.post("/api/posts", json={"text": "hi", "targets": [{"account_id": a}], "action": "now"}, headers=H)
    run_engine()
    t = store.one("SELECT * FROM targets")
    assert t["status"] == "pending" and t["next_try_at"] >= store.now() + 3500


def test_x_plan_rules():
    store.set("x_plan", "free")
    assert limits.rules("x")[0][0] == 17
    store.set("x_plan", "basic")
    assert limits.rules("x")[0][0] == 100
    store.set("x_plan", "free")


def test_limits_api(c):
    connect(c)
    d = c.get("/api/limits").get_json()
    assert d["networks"]["instagram"][0]["limit"] == 100 and d["accounts"]
    assert c.post("/api/limits", json={"overrides": {"x": [[0, 86400]]}}, headers=H).status_code == 400
    c.post("/api/limits", json={"x_plan": "pro", "overrides": {"telegram": [[10, 60]]}}, headers=H)
    assert limits.rules("x")[0][0] == 10000 and limits.rules("telegram")[0][0] == 10
    c.post("/api/limits", json={"x_plan": "free", "overrides": {}}, headers=H)


# ------------------------------------------------------------------ health + expiry
def test_health_check_marks_dead_and_recovers(c):
    a = connect(c)
    Demo.check_fail = "Token revoked"
    try:
        r = c.post("/api/accounts/check", json={}, headers=H).get_json()
        assert r["dead"] == 1
        acc = store.one("SELECT * FROM accounts WHERE id=?", (a,))
        assert acc["status"] == "expired" and acc["last_check_at"]
        assert store.one("SELECT * FROM activity WHERE kind='health' AND level='error'")
    finally:
        Demo.check_fail = ""
    r = c.post("/api/accounts/check", json={"ids": [a]}, headers=H).get_json()
    assert r["healthy"] == 1 and store.one("SELECT status FROM accounts WHERE id=?", (a,))["status"] == "ok"


def test_health_due_logic():
    store.set("health_last_day", time.strftime("%Y-%m-%d"))
    store.execute("UPDATE accounts SET last_check_at=?", (store.now(),))
    assert server.eng.health_due() is False
    store.set("health_last_day", "")
    store.set("health_hour", 0)
    assert server.eng.health_due() is True
    store.set("health_hour", 8)


def test_expiry_notifications_once_per_milestone(c):
    a = connect(c)
    store.execute("UPDATE accounts SET expires_at=? WHERE id=?", (store.now() + 3 * 86400 - 60, a))
    server.eng.refresh_tokens()
    server.eng.refresh_tokens()
    notes = store.rows("SELECT * FROM activity WHERE kind='accounts' AND text LIKE '%expires in%'")
    assert len(notes) == 1 and "3 day" in notes[0]["text"]
    store.execute("UPDATE accounts SET expires_at=? WHERE id=?", (store.now() - 10, a))
    server.eng.refresh_tokens()
    assert store.one("SELECT status FROM accounts WHERE id=?", (a,))["status"] == "expired"
    lg = c.get("/api/logins").get_json()["logins"][0]
    assert lg["days_left"] < 0 and store.one("SELECT 1 FROM activity WHERE level='error' AND text LIKE '%expired%'")


# ------------------------------------------------------------------ inbox end to end
def test_inbox_sync_dedupe_tag_and_reply(c):
    a = connect(c)
    c.post("/api/posts", json={"text": "New latte!", "targets": [{"account_id": a}], "action": "now"}, headers=H)
    run_engine()
    r1 = c.post("/api/inbox/sync", json={}, headers=H).get_json()
    r2 = c.post("/api/inbox/sync", json={}, headers=H).get_json()
    assert r1["new"] >= 2 and r2["new"] == 0
    d = c.get("/api/inbox?status=all").get_json()
    assert d["counts"]["new"] == r1["new"] and any(i["kind"] == "dm" for i in d["items"])
    tags = {i["tag"] for i in d["items"]}
    assert tags & {"question", "complaint", "positive"}
    first = d["items"][0]
    det = c.get(f"/api/inbox/{first['id']}").get_json()
    assert det["item"]["status"] == "read"
    rr = c.post(f"/api/inbox/{first['id']}/reply", json={"text": "Thanks!"}, headers=H).get_json()
    assert rr["ok"] and Demo.replies[-1][1] == "Thanks!"
    assert store.one("SELECT status FROM inbox WHERE id=?", (first["id"],))["status"] == "replied"
    c.post("/api/inbox/bulk", json={"ids": [i["id"] for i in d["items"]], "status": "archived"}, headers=H)
    assert c.get("/api/inbox?status=needs").get_json()["items"] == []
    assert any(s["supported"] for s in d["sources"])


def test_inbox_plan_error_backs_off(c, monkeypatch):
    a = connect(c)

    def boom(*a_, **k):
        raise PublishError("Reading mentions isn't included in your X plan", "plan")
    monkeypatch.setattr(Demo, "inbox_fetch", boom)
    server.eng.sync_inbox(force=True)
    acc = store.one("SELECT * FROM accounts WHERE id=?", (a,))
    assert "X plan" in acc["inbox_error"] and acc["inbox_retry_at"] > store.now() + 80000


def test_ai_reply_basic():
    store.set_secret("anthropic_key", "")
    import ai
    if ai.ollama_ok():
        pytest.skip("Ollama running")
    assert "private message" in ai.run("reply", "How much is the latte?", extra="New latte")["text"]


# ------------------------------------------------------------------ AI content calendar
def test_plan_generate_basic_and_apply(c):
    import ai
    if ai.ollama_ok():
        pytest.skip("Ollama running")
    a = connect(c)
    r = c.post("/api/plan/generate", json={"business": "Café Atlas, specialty coffee in Rabat", "audience": "students and remote workers",
                                           "weeks": 2, "per_week": 3, "accounts": [a]}, headers=H).get_json()
    assert r["ok"] and len(r["items"]) == 6 and all(it["ts"] > store.now() for it in r["items"])
    assert len({it["day"] for it in r["items"]}) == 6
    assert c.get("/api/plans").get_json()["plans"][0]["id"] == r["id"]
    ap = c.post("/api/plan/apply", json={"items": r["items"][:4], "accounts": [a], "mode": "schedule"}, headers=H).get_json()
    assert ap["created"] == 4 and store.scalar("SELECT COUNT(*) FROM posts WHERE source='ai-plan' AND status='scheduled'") == 4
    c.post("/api/plan/apply", json={"items": r["items"][4:], "mode": "ideas"}, headers=H)
    assert store.scalar("SELECT COUNT(*) FROM ideas") == 2
    past = dict(r["items"][0], ts=store.now() - 100)
    assert c.post("/api/plan/apply", json={"items": [past], "accounts": [a], "mode": "schedule"}, headers=H).status_code == 400


def test_plan_with_claude_uses_structured_output(monkeypatch):
    import anthropic

    import ai
    seen = {}

    class Block:
        type = "text"
        text = json.dumps({"posts": [{"day": 0, "pillar": "Tip", "topic": "Brew at home", "caption": "Try this ☕", "hashtags": "coffee rabat",
                                      "media_idea": "pour-over photo", "format": "photo"},
                                     {"day": 99, "pillar": "Offer", "topic": "Weekend", "caption": "Weekend treat", "hashtags": "#offer",
                                      "media_idea": "", "format": "photo"}]})

    class Resp:
        stop_reason, content = "end_turn", [Block()]

    class Msgs:
        def create(self, **kw):
            seen.update(kw)
            return Resp()

    class Client:
        def __init__(self, **kw):
            self.beta = type("B", (), {"messages": Msgs()})()

    monkeypatch.setattr(anthropic, "Anthropic", Client)
    store.set_secret("anthropic_key", "sk-ant-x")
    store.set("ai_provider", "anthropic")
    try:
        r = ai.plan({"business": "Café", "weeks": 1, "per_week": 2})
        assert seen["output_config"]["format"]["type"] == "json_schema" and seen["output_config"]["effort"] == "medium"
        assert seen["model"] == "claude-opus-5-5" and seen["fallbacks"] == "default"
        assert r["items"][0]["hashtags"] == "#coffee #rabat" and r["items"][1]["day"] == 6  # clamped into the week
    finally:
        store.set_secret("anthropic_key", "")
        store.set("ai_provider", "auto")


def test_status_counts_include_inbox_and_expiry(c):
    connect(c)
    st = c.get("/api/status").get_json()["counts"]
    assert {"inbox_new", "inbox_needs", "expiring"} <= set(st)
    _ = engine_mod
