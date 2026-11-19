"""Engine + API end-to-end with the demo network (no internet)."""

import io
import json
import os
import tempfile
import time
from datetime import datetime, timedelta

import pytest
from PIL import Image

import ai
import engine as engine_mod
import server
import store
from connectors.more import Demo

H = {"X-PP": "1"}


@pytest.fixture(autouse=True)
def clean():
    for t in ("posts", "targets", "accounts", "media", "ideas", "feeds", "activity", "inbox", "plans"):
        store.execute(f"DELETE FROM {t}")
    Demo.fail_next = []
    yield


@pytest.fixture
def c():
    return server.app.test_client()


def connect(c, name="Main"):
    r = c.post("/api/accounts/connect", json={"network": "demo", "data": {"name": name}}, headers=H).get_json()
    return r["added"][0]


def upload(c, w=1200, h=1200, name="a.png"):
    b = io.BytesIO()
    Image.new("RGBA", (w, h), (0, 120, 255, 255)).save(b, "PNG")
    b.seek(0)
    return c.post("/api/media", data={"files": [(b, name)]}, headers=H, content_type="multipart/form-data").get_json()["media"][0]


def run_engine():
    server.eng.dispatch(wait=True)


def test_guards(c):
    assert c.post("/api/posts", json={}).status_code == 403
    assert c.get("/api/status", headers={"Host": "evil.com"}).status_code == 403
    assert c.get("/m/nope/x.jpg", headers={"Host": "abc.trycloudflare.com"}).status_code == 404  # public path reachable, unknown token


def test_post_now_publishes(c):
    a = connect(c)
    r = c.post("/api/posts", json={"text": "Hello world", "targets": [{"account_id": a}], "action": "now"}, headers=H).get_json()
    run_engine()
    p = c.get(f"/api/posts/{r['post']['id']}").get_json()["post"]
    assert p["status"] == "published" and p["targets"][0]["status"] == "published" and p["targets"][0]["url"].startswith("https://example.com/demo/")


def test_schedule_validation(c):
    a = connect(c)
    assert c.post("/api/posts", json={"text": "x", "targets": [], "action": "now"}, headers=H).status_code == 400
    r = c.post("/api/posts", json={"text": "x", "targets": [{"account_id": a}], "action": "schedule", "scheduled_at": store.now() - 3600}, headers=H)
    assert r.status_code == 400 and "past" in r.get_json()["error"]
    r = c.post("/api/posts", json={"text": "", "targets": [{"account_id": a}], "action": "now"}, headers=H)
    assert r.status_code == 400


def test_draft_and_future_schedule_not_published(c):
    a = connect(c)
    d = c.post("/api/posts", json={"text": "draft", "targets": [{"account_id": a}], "action": "draft"}, headers=H).get_json()["post"]
    s = c.post("/api/posts", json={"text": "later", "targets": [{"account_id": a}], "action": "schedule",
                                   "scheduled_at": store.now() + 3600}, headers=H).get_json()["post"]
    run_engine()
    assert store.one("SELECT status FROM posts WHERE id=?", (d["id"],))["status"] == "draft"
    assert store.one("SELECT status FROM posts WHERE id=?", (s["id"],))["status"] == "scheduled"


def test_partial_failure_and_retry(c):
    a1, a2 = connect(c, "One"), connect(c, "Two")
    r = c.post("/api/posts", json={"text": "hi", "targets": [{"account_id": a1}, {"account_id": a2, "text": "this will FAIL"}],
                                   "action": "now"}, headers=H).get_json()
    run_engine()
    p = c.get(f"/api/posts/{r['post']['id']}").get_json()["post"]
    assert p["status"] == "partial"
    bad = next(t for t in p["targets"] if t["status"] == "failed")
    assert "FAIL" in bad["error"]
    store.execute("UPDATE targets SET text='fixed' WHERE id=?", (bad["id"],))
    c.post(f"/api/posts/{p['id']}/action", json={"action": "retry"}, headers=H)
    run_engine()
    assert store.one("SELECT status FROM posts WHERE id=?", (p["id"],))["status"] == "published"


def test_transient_errors_back_off_then_fail(c):
    a = connect(c)
    Demo.fail_next = ["transient"]
    r = c.post("/api/posts", json={"text": "hi", "targets": [{"account_id": a}], "action": "now"}, headers=H).get_json()
    run_engine()
    t = store.one("SELECT * FROM targets WHERE post_id=?", (r["post"]["id"],))
    assert t["status"] == "pending" and t["next_try_at"] > store.now() + 30 and t["attempts"] == 1
    store.execute("UPDATE targets SET next_try_at=0")
    run_engine()
    assert store.one("SELECT status FROM posts WHERE id=?", (r["post"]["id"],))["status"] == "published"


def test_auth_error_marks_account_expired(c):
    a = connect(c)
    Demo.fail_next = ["auth"]
    c.post("/api/posts", json={"text": "hi", "targets": [{"account_id": a}], "action": "now"}, headers=H)
    run_engine()
    assert store.one("SELECT status FROM accounts WHERE id=?", (a,))["status"] == "expired"
    r = c.post("/api/posts", json={"text": "again", "targets": [{"account_id": a}], "action": "now"}, headers=H)
    assert r.status_code == 400 and "reconnect" in r.get_json()["error"].lower()


def test_queue_uses_slots_and_avoids_taken(c):
    a = connect(c)
    c.post(f"/api/accounts/{a}", json={"slots": [{"days": "0123456", "time": "09:00"}, {"days": "0123456", "time": "17:00"}]}, headers=H)
    r1 = c.post("/api/posts", json={"text": "1", "targets": [{"account_id": a}], "action": "queue"}, headers=H).get_json()["post"]
    r2 = c.post("/api/posts", json={"text": "2", "targets": [{"account_id": a}], "action": "queue"}, headers=H).get_json()["post"]
    t1, t2 = datetime.fromtimestamp(r1["scheduled_at"]), datetime.fromtimestamp(r2["scheduled_at"])
    assert {t1.strftime("%H:%M"), t2.strftime("%H:%M")} <= {"09:00", "17:00"} and r2["scheduled_at"] > r1["scheduled_at"] > store.now()


def test_recurring_post_reschedules(c):
    a = connect(c)
    r = c.post("/api/posts", json={"text": "weekly", "targets": [{"account_id": a}], "action": "now",
                                   "recurring": {"every_days": 7, "times": 3}}, headers=H).get_json()
    run_engine()
    nxt = store.rows("SELECT * FROM posts WHERE status='scheduled'")
    assert len(nxt) == 1 and nxt[0]["scheduled_at"] >= store.now() + 6 * 86400 and store.jd(nxt[0]["recurring"])["done"] == 1
    assert store.scalar("SELECT COUNT(*) FROM targets WHERE post_id=?", (nxt[0]["id"],)) == 1
    del r


def test_media_upload_prepare_and_validation(c):
    a = connect(c)
    m = upload(c, 3000, 600, "wide.png")
    assert m["kind"] == "image" and m["thumb"].endswith("/thumb")
    row = store.one("SELECT * FROM media WHERE id=?", (m["id"],))
    import media as media_mod
    ig = media_mod.prepare(row, "instagram")
    w, h = media_mod.dims(ig)
    assert ig.endswith(".jpg") and 0.8 <= w / h <= 1.91 + 0.01 and max(w, h) <= 1440
    bs = media_mod.prepare(row, "bluesky")
    assert os.path.getsize(bs) <= 950_000
    r = c.post("/api/posts", json={"text": "pic", "media": [m["id"]], "targets": [{"account_id": a}], "action": "now"}, headers=H).get_json()
    run_engine()
    assert store.one("SELECT status FROM posts WHERE id=?", (r["post"]["id"],))["status"] == "published"
    assert c.delete(f"/api/media/{m['id']}", headers=H).status_code == 200


def test_validate_endpoint_reports_per_account(c):
    a = connect(c)
    r = c.post("/api/posts/validate", json={"text": "x" * 1200, "targets": [{"account_id": a}]}, headers=H).get_json()
    assert any("too long" in p for p in r["problems"][str(a)])


def test_reminder_mode_flow(c):
    r = c.post("/api/accounts/connect", json={"network": "reminder", "data": {"name": "TikTok @me", "app": "TikTok"}}, headers=H).get_json()
    aid = r["added"][0]
    p = c.post("/api/posts", json={"text": "Dance video!", "link": "https://x.com", "targets": [{"account_id": aid}], "action": "now"}, headers=H).get_json()
    run_engine()
    rem = c.get("/api/reminders").get_json()["reminders"]
    assert len(rem) == 1 and "https://x.com" in rem[0]["text"] and rem[0]["app"] == "TikTok"
    assert store.one("SELECT status FROM posts WHERE id=?", (p["post"]["id"],))["status"] == "published"
    c.post(f"/api/targets/{rem[0]['target_id']}/done", json={"url": "https://tiktok.com/@me/video/1"}, headers=H)
    assert c.get("/api/reminders").get_json()["reminders"] == []


def test_edit_published_post_blocked_and_duplicate(c):
    a = connect(c)
    p = c.post("/api/posts", json={"text": "v1", "targets": [{"account_id": a}], "action": "now"}, headers=H).get_json()["post"]
    run_engine()
    r = c.post("/api/posts", json={"id": p["id"], "text": "v2", "targets": [{"account_id": a}], "action": "now"}, headers=H)
    assert r.status_code == 400
    d = c.post(f"/api/posts/{p['id']}/action", json={"action": "duplicate"}, headers=H).get_json()
    assert store.one("SELECT status,text FROM posts WHERE id=?", (d["id"],)) == {"status": "draft", "text": "v1"}


def test_move_and_unschedule(c):
    a = connect(c)
    p = c.post("/api/posts", json={"text": "x", "targets": [{"account_id": a}], "action": "schedule", "scheduled_at": store.now() + 7200},
               headers=H).get_json()["post"]
    new = store.now() + 86400
    c.post(f"/api/posts/{p['id']}/action", json={"action": "move", "scheduled_at": new}, headers=H)
    assert store.one("SELECT scheduled_at FROM posts WHERE id=?", (p["id"],))["scheduled_at"] == new
    assert c.post(f"/api/posts/{p['id']}/action", json={"action": "move", "scheduled_at": 5}, headers=H).status_code == 400
    c.post(f"/api/posts/{p['id']}/action", json={"action": "unschedule"}, headers=H)
    assert store.one("SELECT status FROM posts WHERE id=?", (p["id"],))["status"] == "draft"


def test_csv_import(c):
    a = connect(c, "Main")
    when = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    csv = f"text,date,accounts,media,link\n\"Post one\",{when},demo,,https://a.com\n\"Post two\",,all,,\n\"Bad date\",tomorrow,demo,,\n"
    r = c.post("/api/import/csv", data={"file": (io.BytesIO(csv.encode()), "p.csv")}, headers=H, content_type="multipart/form-data").get_json()
    assert r["created"] == 2 and len(r["errors"]) == 1
    assert store.scalar("SELECT COUNT(*) FROM targets WHERE account_id=?", (a,)) == 2


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title>
<item><title>First &amp; best</title><link>https://blog.com/1</link><guid>1</guid><description>&lt;p&gt;Hello&lt;/p&gt;</description></item>
<item><title>Second</title><link>https://blog.com/2</link><guid>2</guid></item></channel></rss>"""


def test_rss_feed_skips_existing_then_posts_new(c, fake):
    a = connect(c)
    fake.on("GET", "blog.com/feed", body=RSS)
    r = c.post("/api/feeds", json={"url": "https://blog.com/feed", "accounts": [a], "mode": "queue"}, headers=H).get_json()
    assert r["items"] == 2
    fid = store.scalar("SELECT id FROM feeds")
    server.eng.check_feeds(force_id=fid)
    assert store.scalar("SELECT COUNT(*) FROM posts WHERE source='rss'") == 0  # existing items are not flooded out
    fake.rules.clear()
    fake.on("GET", "blog.com/feed", body=RSS.replace("<item><title>First", "<item><title>Brand new</title><link>https://blog.com/3</link><guid>3</guid></item><item><title>First"))
    server.eng.check_feeds(force_id=fid)
    p = store.one("SELECT * FROM posts WHERE source='rss'")
    assert p and "Brand new" in p["text"] and "https://blog.com/3" in p["text"] and p["status"] == "scheduled"


def test_metrics_refresh_and_analytics(c):
    a = connect(c)
    p = c.post("/api/posts", json={"text": "stats", "targets": [{"account_id": a}], "action": "now"}, headers=H).get_json()["post"]
    run_engine()
    store.execute("UPDATE targets SET published_at=? WHERE post_id=?", (store.now() - 3600, p["id"]))
    assert server.eng.refresh_metrics() == 1
    an = c.get("/api/analytics?days=30").get_json()
    assert an["total_posts"] == 1 and an["totals"]["likes"] > 0 and an["top"][0]["engagement"] > 0 and len(an["heat"]) == 168


def test_best_times_defaults():
    b = engine_mod.best_times(["linkedin", "instagram"])
    assert b[0]["network"] == "linkedin" and b[0]["learned"] is False and b[0]["hours"]


def test_ai_basic_fallback_and_off(c):
    store.set("ai_provider", "auto")
    store.set_secret("anthropic_key", "")
    if ai.ollama_ok():
        pytest.skip("Ollama running on this machine")
    r = c.post("/api/ai", json={"task": "hashtags", "text": "Handmade leather wallets for travellers, crafted in Fes"}, headers=H).get_json()
    assert r["provider"] == "basic" and "#leather" in r["text"]
    r = c.post("/api/ai", json={"task": "write", "text": "x"}, headers=H)
    assert r.status_code == 400 and "Claude API key" in r.get_json()["error"]
    store.set("ai_provider", "off")
    assert "turned off" in c.post("/api/ai", json={"task": "hashtags", "text": "x"}, headers=H).get_json()["error"]
    store.set("ai_provider", "auto")


def test_ai_anthropic_call_shape(monkeypatch):
    """The Claude request uses the current model, low effort and server-side fallbacks; refusals are handled."""
    import anthropic
    seen = {}

    class Block:
        type, text = "text", "#coffee #morning"

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
    store.set_secret("anthropic_key", "sk-ant-test")
    store.set("ai_provider", "anthropic")
    try:
        r = ai.run("hashtags", "Morning coffee ritual")
        assert r["text"] == "#coffee #morning" and r["provider"] == "anthropic"
        assert seen["model"] == "claude-opus-5-5" and seen["fallbacks"] == "default" and seen["output_config"] == {"effort": "low"}
        Resp.stop_reason = "refusal"
        with pytest.raises(ai.AIError, match="declined"):
            ai.run("hashtags", "x")
    finally:
        store.set_secret("anthropic_key", "")
        store.set("ai_provider", "auto")


def test_brands_isolate_accounts(c):
    a = connect(c, "Brand1 acc")
    c.post("/api/brands", json={"name": "Client B"}, headers=H)
    assert c.get("/api/accounts").get_json()["accounts"] == []
    connect(c, "B acc")
    first = store.scalar("SELECT MIN(id) FROM brands")
    c.post("/api/brands/select", json={"id": first}, headers=H)
    assert [x["id"] for x in c.get("/api/accounts").get_json()["accounts"]] == [a]


def test_backup_restore(c):
    connect(c, "Keep")
    data = c.get("/api/backup").data
    store.execute("DELETE FROM accounts")
    r = c.post("/api/restore", data={"file": (io.BytesIO(data), "b.db")}, headers=H, content_type="multipart/form-data")
    assert r.status_code == 200 and store.one("SELECT name FROM accounts")["name"] == "Keep"


def test_all_list_endpoints(c):
    connect(c)
    for u in ("/api/status", "/api/dashboard", "/api/networks", "/api/accounts", "/api/media", "/api/posts", "/api/posts?status=scheduled",
              "/api/ideas", "/api/hashtag_groups", "/api/templates", "/api/feeds", "/api/analytics", "/api/settings", "/api/activity",
              "/api/reminders", "/api/best-times?networks=x,linkedin", "/api/queue/next", "/api/import/template", "/", "/static/app.js"):
        assert c.get(u).status_code == 200, u


def test_public_media_route_serves_registered_file(c):
    p = os.path.join(tempfile.mkdtemp(), "x.jpg")
    Image.new("RGB", (10, 10)).save(p)
    engine_mod.PUBLIC["tok123"] = p
    assert c.get("/m/tok123/x.jpg", headers={"Host": "abc.trycloudflare.com"}).status_code == 200
    assert c.get("/m/tok123/other.jpg").status_code == 404
    assert c.post("/m/tok123/x.jpg", headers={"Host": "abc.trycloudflare.com"}).status_code in (403, 405)
    time.sleep(0)
    json.dumps({})


def test_native_schedule_hands_off_then_goes_live(c, monkeypatch):
    monkeypatch.setattr(Demo, "native_schedule", (60, 30 * 86400), raising=False)
    seen = []
    orig = Demo.publish
    monkeypatch.setattr(Demo, "publish", lambda self, a, cr, p: (seen.append(p.get("publish_at")), orig(self, a, cr, p))[1])
    a = connect(c)
    when = store.now() + 3 * 3600
    p = c.post("/api/posts", json={"text": "later", "targets": [{"account_id": a}], "action": "schedule", "scheduled_at": when}, headers=H).get_json()["post"]
    server.eng.handoff(wait=True)
    server.eng.wait_idle()
    t = c.get(f"/api/posts/{p['id']}").get_json()["post"]
    assert seen == [when] and t["status"] == "scheduled" and t["targets"][0]["status"] == "handed" and t["targets"][0]["published_at"] == when
    # can't be moved any more, and the normal dispatcher leaves it alone
    r = c.post(f"/api/posts/{p['id']}/action", json={"action": "move", "scheduled_at": when + 600}, headers=H)
    assert r.status_code == 400 and "already uploaded" in r.get_json()["error"]
    store.execute("UPDATE posts SET scheduled_at=? WHERE id=?", (store.now() - 5, p["id"]))
    store.execute("UPDATE targets SET published_at=? WHERE post_id=?", (store.now() - 5, p["id"]))
    run_engine()
    assert len(seen) == 1
    server.eng.go_live()
    t = c.get(f"/api/posts/{p['id']}").get_json()["post"]
    assert t["status"] == "published" and t["targets"][0]["status"] == "published"


def test_native_schedule_off_or_refused_falls_back(c, monkeypatch):
    monkeypatch.setattr(Demo, "native_schedule", (60, 30 * 86400), raising=False)
    a = connect(c)
    when = store.now() + 3 * 3600
    p = c.post("/api/posts", json={"text": "later", "targets": [{"account_id": a}], "action": "schedule", "scheduled_at": when}, headers=H).get_json()["post"]
    store.set("native_schedule", 0)
    try:
        assert server.eng.handoff_candidates() == []
    finally:
        store.set("native_schedule", 1)
    Demo.fail_next = ["permanent"]
    server.eng.handoff(wait=True)
    server.eng.wait_idle()
    t = c.get(f"/api/posts/{p['id']}").get_json()["post"]["targets"][0]
    assert t["status"] == "pending"  # refused by the network's scheduler → stays queued for the normal publish at its time
    assert server.eng.handoff_candidates() == []  # and isn't retried natively
