"""v1.3: brands fully isolated (own developer apps + settings), a fair scheduler for hundreds of posts, bulk scheduling,
bulk actions, paged lists, resumable YouTube uploads, and the change counter the UI uses."""

import json
import os
import tempfile
import time
from datetime import datetime

import pytest

import engine as engine_mod
import limits
import server
import store
import vault
from connectors.more import Demo, YouTube, resumable_upload
from net import PublishError
from test_app import H, c, clean, connect, run_engine, upload  # noqa: F401 - fixtures
from test_connectors import ACC  # noqa: F401


@pytest.fixture(autouse=True)
def brands_reset():
    """Every test starts with one brand (id of the first) as the current brand, and no brand apps."""
    first = store.scalar("SELECT MIN(id) FROM brands")
    for b in store.rows("SELECT id FROM brands WHERE id<>?", (first,)):
        store.execute("DELETE FROM brands WHERE id=?", (b["id"],))
    store.execute("DELETE FROM brand_apps")
    store.execute("DELETE FROM settings WHERE key LIKE 'b%:%'")
    store.set("brand_id", first)
    store.set("parallel", 4)
    store.set("video_parallel", 2)
    yield


def new_brand(c, name):
    return c.post("/api/brands", json={"name": name}, headers=H).get_json()["id"]


def select(c, bid):
    assert c.post("/api/brands/select", json={"id": bid}, headers=H).get_json()["ok"]


def video(name="clip.mp4", brand=None):
    """A video row in the library (a small file is enough: the demo network doesn't upload it)."""
    p = os.path.join(tempfile.mkdtemp(), name)
    with open(p, "wb") as f:
        f.write(b"\x00" * 2048)
    bid = brand or store.get("brand_id")
    mid = store.execute("INSERT INTO media(brand_id,name,path,kind,mime,size,created_at,token) VALUES(?,?,?,?,?,?,?,?)",
                        (bid, name, p, "video", "video/mp4", 2048, store.now(), "t"))
    return mid


# ------------------------------------------------------------------ brands: their own developer apps
def test_every_brand_has_its_own_app_keys(c):
    a = store.get("brand_id")
    assert c.post("/api/brand/apps/youtube", json={"client_id": "A-client", "client_secret": "A-secret"}, headers=H).get_json()["ok"]
    b = new_brand(c, "Client B")           # creating a brand switches to it
    d = c.get("/api/brand").get_json()
    yt = next(x for x in d["apps"] if x["key"] == "youtube")
    assert d["brand"]["id"] == b and yt["saved"]["client_id"] == "" and not yt["complete"], "a new brand starts empty: nothing shared"
    c.post("/api/brand/apps/youtube", json={"client_id": "B-client", "client_secret": "B-secret"}, headers=H)
    assert store.app_creds("youtube", a)["client_id"] == "A-client"
    assert store.app_creds("youtube", b)["client_id"] == "B-client"
    # the secret is never sent back, only "saved"
    yt = next(x for x in c.get("/api/brand").get_json()["apps"] if x["key"] == "youtube")
    assert yt["saved"]["client_secret"] is True and "B-secret" not in json.dumps(c.get("/api/brand").get_json())
    # the connect dialog shows the CURRENT brand's app
    nets = {n["key"]: n for n in c.get("/api/networks").get_json()["networks"]}
    assert nets["youtube"]["app_saved"]["client_id"] == "B-client"
    select(c, a)
    nets = {n["key"]: n for n in c.get("/api/networks").get_json()["networks"]}
    assert nets["youtube"]["app_saved"]["client_id"] == "A-client"
    # copy all apps from another brand in one click
    cbr = new_brand(c, "Client C")
    assert c.post("/api/brand/apps-copy", json={"from": b}, headers=H).get_json()["copied"] == 1
    assert store.app_creds("youtube", cbr)["client_id"] == "B-client"
    # clear one
    c.post("/api/brand/apps/youtube", json={"clear": True}, headers=H)
    assert store.app_creds("youtube", cbr) == {}


def test_old_shared_apps_are_copied_to_existing_brands_once():
    """Upgrade from 1.2: the one shared app goes to every brand that exists, so nothing stops working."""
    first = store.get("brand_id")
    other = store.execute("INSERT INTO brands(name,color,created_at) VALUES('Old 2','#000',?)", (store.now(),))
    store.execute("DELETE FROM settings WHERE key='brand_apps_v1'")
    store.execute("DELETE FROM apps")
    store.execute("INSERT INTO apps(network,creds) VALUES(?,?)", ("youtube", vault.encrypt(json.dumps({"client_id": "legacy", "client_secret": "s"}))))
    store.init()
    assert store.app_creds("youtube", first)["client_id"] == "legacy" and store.app_creds("youtube", other)["client_id"] == "legacy"
    # runs only once: a brand made later stays empty
    later = store.execute("INSERT INTO brands(name,color,created_at) VALUES('New','#000',?)", (store.now(),))
    store.init()
    assert store.app_creds("youtube", later) == {}
    store.execute("DELETE FROM apps")


def test_account_keeps_the_app_it_was_connected_with(c, fake):
    """Changing a brand's keys later must not break a channel that logged in with the old ones."""
    c.post("/api/brand/apps/youtube", json={"client_id": "OLD", "client_secret": "S1"}, headers=H)
    bid = store.get("brand_id")
    aid = server._add_accounts([{"network": "youtube", "name": "Chan", "external_id": "UC1", "creds": {"token": "t", "refresh": "R", "token_exp": 0}}], bid)[0]
    c.post("/api/brand/apps/youtube", json={"client_id": "NEW", "client_secret": "S2"}, headers=H)
    fake.on("POST", "oauth2.googleapis.com/token", body={"access_token": "fresh", "expires_in": 3600})
    a = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
    assert YouTube()._fresh(a, store.creds_of(a)) == "fresh"
    assert fake.find("oauth2.googleapis.com/token")[0]["data"]["client_id"] == "OLD"


def test_oauth_login_lands_in_the_brand_it_started_in(c, monkeypatch):
    """Start 'Log in with Google' in brand A, switch to brand B before finishing: the channel still goes to A."""
    a = store.get("brand_id")
    c.post("/api/brand/apps/youtube", json={"client_id": "A", "client_secret": "s"}, headers=H)
    st = c.post("/api/oauth/start", json={"network": "youtube"}, headers=H).get_json()["state"]
    b = new_brand(c, "Other")
    seen = {}

    def finish(self, app, redirect, code):
        seen["app"] = app
        return [{"network": "youtube", "name": "Chan A", "external_id": "UCA", "creds": {"token": "t", "refresh": "r", "token_exp": 0}}]
    monkeypatch.setattr(YouTube, "oauth_finish", finish)
    c.get(f"/oauth/callback?state={st}&code=xyz")
    r = c.get(f"/api/oauth/result?state={st}").get_json()
    assert r["status"] == "done" and seen["app"]["client_id"] == "A"
    acc = store.one("SELECT * FROM accounts WHERE id=?", (r["added"][0],))
    assert acc["brand_id"] == a and acc["brand_id"] != b
    assert store.creds_of(acc)["_app"]["client_id"] == "A"


def test_brand_settings_are_separate(c):
    a = store.get("brand_id")
    c.post("/api/settings", json={"utm_campaign": "brand-a", "x_plan": "pro", "native_schedule": 0}, headers=H)
    c.post("/api/settings/slots", json={"slots": [{"days": "0123456", "time": "07:15"}]}, headers=H)
    b = new_brand(c, "B")
    s = c.get("/api/settings").get_json()
    assert s["utm_campaign"] == "postpilot" and s["x_plan"] == "free" and s["native_schedule"] == 1, "a new brand starts from the defaults"
    assert [x["time"] for x in c.get("/api/accounts").get_json()["default_slots"]] == ["09:00", "13:00", "18:00"]
    select(c, a)
    s = c.get("/api/settings").get_json()
    assert s["utm_campaign"] == "brand-a" and s["x_plan"] == "pro" and s["native_schedule"] == 0
    assert c.get("/api/accounts").get_json()["default_slots"][0]["time"] == "07:15"
    assert limits.rules("x", a)[0][0] == 10000 and limits.rules("x", b)[0][0] == 17
    assert b


def test_deleting_a_brand_removes_its_apps_settings_and_media(c):
    keep = store.get("brand_id")
    b = new_brand(c, "Temp")
    c.post("/api/brand/apps/youtube", json={"client_id": "T", "client_secret": "s"}, headers=H)
    c.post("/api/settings", json={"utm_campaign": "tmp"}, headers=H)
    m = upload(c)
    assert c.delete(f"/api/brands/{b}", headers=H).get_json()["ok"]
    assert not store.rows("SELECT * FROM brand_apps WHERE brand_id=?", (b,))
    assert not store.rows("SELECT * FROM settings WHERE key LIKE ?", (f"b{b}:%",))
    assert not store.one("SELECT 1 FROM media WHERE id=?", (m["id"],))
    assert store.get("brand_id") == keep or store.one("SELECT 1 FROM brands WHERE id=?", (store.get("brand_id"),))


# ------------------------------------------------------------------ the scheduler under load
def test_one_busy_account_never_blocks_the_others(c):
    big = connect(c, "Big channel")
    small = connect(c, "Small brand")
    past = store.now() - 60
    for i in range(60):    # 60 overdue posts on one account, created first
        c.post("/api/posts", json={"text": f"big {i}", "targets": [{"account_id": big}], "action": "schedule", "scheduled_at": store.now() + 5}, headers=H)
    c.post("/api/posts", json={"text": "small one", "targets": [{"account_id": small}], "action": "schedule", "scheduled_at": store.now() + 5}, headers=H)
    store.execute("UPDATE posts SET scheduled_at=?", (past,))
    due = server.eng.due_targets(exclude={big})
    assert [t["account_id"] for t in due] == [small], "the small account is found even with 60 older posts on a busy account"
    due = server.eng.due_targets()
    assert sorted(t["account_id"] for t in due) == sorted([big, small]), "one (the oldest) per account"


def test_hundreds_of_posts_all_go_out(c):
    accs = [connect(c, f"Acc {i}") for i in range(6)]
    for i in range(240):
        c.post("/api/posts", json={"text": f"post {i}", "targets": [{"account_id": accs[i % 6]}], "action": "schedule",
                                   "scheduled_at": store.now() + 5}, headers=H)
    store.execute("UPDATE posts SET scheduled_at=?", (store.now() - 5,))
    t0 = time.time()
    for _ in range(200):
        run_engine()
        if not store.scalar("SELECT COUNT(*) FROM targets WHERE status<>'published'", (), 0):
            break
    assert store.scalar("SELECT COUNT(*) FROM targets WHERE status='published'", (), 0) == 240
    assert store.scalar("SELECT COUNT(*) FROM posts WHERE status='published'", (), 0) == 240
    assert time.time() - t0 < 60


def test_parallel_setting_limits_how_many_publish_at_once(c, monkeypatch):
    store.set("parallel", 2)
    server.eng.workers = 2
    accs = [connect(c, f"P{i}") for i in range(5)]
    running, peak = [0], [0]
    orig = Demo.publish

    def slow(self, a, cr, p):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        time.sleep(0.15)
        running[0] -= 1
        return orig(self, a, cr, p)
    monkeypatch.setattr(Demo, "publish", slow)
    for a in accs:
        c.post("/api/posts", json={"text": "x", "targets": [{"account_id": a}], "action": "now"}, headers=H)
    for _ in range(10):
        server.eng.dispatch()
        time.sleep(0.05)
    server.eng.wait_idle()
    for _ in range(20):
        run_engine()
    assert peak[0] <= 2 and store.scalar("SELECT COUNT(*) FROM targets WHERE status='published'", (), 0) == 5


def test_paused_account_waits_instead_of_failing(c):
    a = connect(c)
    c.post(f"/api/accounts/{a}", json={"paused": True}, headers=H)
    r = c.post("/api/posts", json={"text": "wait for me", "targets": [{"account_id": a}], "action": "now"}, headers=H).get_json()
    run_engine()
    t = store.one("SELECT * FROM targets WHERE post_id=?", (r["post"]["id"],))
    assert t["status"] == "pending" and "paused" in t["error"] and t["attempts"] == 0
    c.post(f"/api/accounts/{a}", json={"paused": False}, headers=H)
    run_engine()
    assert store.one("SELECT status FROM targets WHERE post_id=?", (r["post"]["id"],))["status"] == "published"


def test_slow_background_jobs_do_not_block_publishing(c, monkeypatch):
    a = connect(c)
    monkeypatch.setattr(server.eng, "refresh_metrics", lambda *x, **k: time.sleep(3))
    server.eng.next_metrics = 0
    c.post("/api/posts", json={"text": "fast", "targets": [{"account_id": a}], "action": "now"}, headers=H)
    t0 = time.time()
    server.eng.tick()
    assert time.time() - t0 < 1.5, "metrics run in their own thread"
    server.eng.wait_idle()
    assert store.scalar("SELECT COUNT(*) FROM targets WHERE status IN ('published','publishing')", (), 0) == 1


def test_video_lanes_limit_big_uploads(c):
    store.set("video_parallel", 1)
    assert server.eng._video_lane() is True
    assert server.eng._video_lane() is False, "only one video upload at a time with this setting"
    server.eng._video_done()
    assert server.eng._video_lane() is True
    server.eng._video_done()


# ------------------------------------------------------------------ bulk scheduling
def test_bulk_schedule_100_videos_in_queue_slots(c):
    a = connect(c, "Channel")
    mids = [video(f"How_to-use part {i}.mp4") for i in range(100)]
    r = c.post("/api/posts/bulk", json={"media": mids, "accounts": [a], "mode": "queue", "caption": "{name} #shorts",
                                        "title": "{name} ({n}/{total})", "privacy": "unlisted", "per_day": 2}, headers=H).get_json()
    assert r["ok"] and r["created"] == 100 and not r["problems"]
    posts = store.rows("SELECT * FROM posts WHERE source='bulk' ORDER BY scheduled_at")
    assert len(posts) == 100 and posts[0]["text"] == "How to use part 0 #shorts"
    times = [p["scheduled_at"] for p in posts]
    assert len(set(times)) == 100 and min(times) > store.now()
    per_day = {}
    for t in times:
        per_day[datetime.fromtimestamp(t).date()] = per_day.get(datetime.fromtimestamp(t).date(), 0) + 1
    assert max(per_day.values()) <= 2, "never more than per_day on one day"
    opts = json.loads(store.one("SELECT options FROM targets WHERE post_id=?", (posts[0]["id"],))["options"])
    assert opts["title"] == "How to use part 0 (1/100)" and opts["privacy"] == "unlisted"
    # the queue already has these: a second batch goes after, never on the same slots
    r2 = c.post("/api/posts/bulk", json={"media": [video("next.mp4")], "accounts": [a], "mode": "queue"}, headers=H).get_json()
    t2 = store.one("SELECT scheduled_at FROM posts WHERE id=?", (r2["ids"][0],))["scheduled_at"]
    assert all(abs(t2 - t) >= 600 for t in times)


def test_bulk_spread_drafts_and_checks(c):
    a = connect(c)
    mids = [video(f"v{i}.mp4") for i in range(7)]
    start = store.now() + 3600
    r = c.post("/api/posts/bulk", json={"media": mids, "accounts": [a], "mode": "spread", "start_at": start, "every_min": 30, "per_day": 3}, headers=H).get_json()
    times = sorted(p["scheduled_at"] for p in store.rows("SELECT scheduled_at FROM posts WHERE id IN (%s)" % ",".join(map(str, r["ids"]))))
    assert times[0] == start and times[1] == start + 1800 and times[2] == start + 3600
    assert datetime.fromtimestamp(times[3]).date() != datetime.fromtimestamp(times[2]).date(), "4th one moves to the next day"
    d = c.post("/api/posts/bulk", json={"media": mids[:2], "accounts": [a], "mode": "draft"}, headers=H).get_json()
    assert all(p["status"] == "draft" and p["scheduled_at"] is None for p in store.rows("SELECT * FROM posts WHERE id IN (%s)" % ",".join(map(str, d["ids"]))))
    # past start, other brand's account, nothing selected -> clear errors
    assert c.post("/api/posts/bulk", json={"media": mids, "accounts": [a], "mode": "spread", "start_at": store.now() - 10}, headers=H).status_code == 400
    assert c.post("/api/posts/bulk", json={"media": [], "accounts": [a]}, headers=H).status_code == 400
    other = new_brand(c, "Other")
    r = c.post("/api/posts/bulk", json={"media": mids, "accounts": [a]}, headers=H)
    assert r.status_code == 400 and "brand" in r.get_json()["error"]
    assert other


def test_bulk_skips_files_a_network_refuses(c, fake):
    bid = store.get("brand_id")
    yt = server._add_accounts([{"network": "youtube", "name": "YT", "external_id": "UC9", "creds": {"token": "t", "token_exp": 9999999999}}], bid)[0]
    pic = upload(c)["id"]
    vid = video("ok.mp4")
    r = c.post("/api/posts/bulk", json={"media": [pic, vid], "accounts": [yt], "mode": "draft"}, headers=H).get_json()
    assert r["created"] == 1 and "needs a video" in r["problems"][0]
    # YouTube ~6 uploads a day: 10 on one day -> warned, still scheduled (they wait their turn)
    many = [video(f"m{i}.mp4") for i in range(10)]
    r = c.post("/api/posts/bulk", json={"media": many, "accounts": [yt], "mode": "spread", "start_at": store.now() + 600, "every_min": 5}, headers=H).get_json()
    assert r["created"] == 10 and r["warnings"] and "YouTube" in r["warnings"][0]


# ------------------------------------------------------------------ bulk actions, paging, speed
def test_bulk_actions(c):
    a = connect(c)
    ids = [c.post("/api/posts", json={"text": f"p{i}", "targets": [{"account_id": a}], "action": "schedule",
                                     "scheduled_at": store.now() + 7200 + i * 60}, headers=H).get_json()["post"]["id"] for i in range(30)]
    r = c.post("/api/posts/bulk-action", json={"ids": ids[:10], "action": "shift", "minutes": 60}, headers=H).get_json()
    assert r["done"] == 10
    r = c.post("/api/posts/bulk-action", json={"ids": ids[10:20], "action": "unschedule"}, headers=H).get_json()
    assert r["done"] == 10 and store.scalar("SELECT COUNT(*) FROM posts WHERE status='draft'", (), 0) == 10
    r = c.post("/api/posts/bulk-action", json={"ids": ids[20:], "action": "delete"}, headers=H).get_json()
    assert r["done"] == 10 and store.scalar("SELECT COUNT(*) FROM posts", (), 0) == 20
    assert store.scalar("SELECT COUNT(*) FROM targets", (), 0) == 20
    assert c.post("/api/posts/bulk-action", json={"ids": ids, "action": "explode"}, headers=H).status_code == 400


def test_posts_are_paged_and_loaded_in_batches(c):
    a = connect(c)
    m = upload(c)
    for i in range(150):
        c.post("/api/posts", json={"text": f"p{i}", "media": [m["id"]], "targets": [{"account_id": a}], "action": "schedule",
                                   "scheduled_at": store.now() + 3600 + i * 60}, headers=H)
    d = c.get("/api/posts?status=scheduled").get_json()
    assert len(d["posts"]) == 100 and d["total"] == 150 and d["has_more"]
    assert d["posts"][0]["media_items"][0]["id"] == m["id"] and d["posts"][0]["targets"][0]["account_id"] == a
    d2 = c.get("/api/posts?status=scheduled&offset=100").get_json()
    assert len(d2["posts"]) == 50 and not d2["has_more"] and d2["posts"][0]["text"] == "p100"
    # the calendar range is not cut at 100
    cal = c.get(f"/api/posts?from={store.now()}&to={store.now() + 86400 * 2}").get_json()
    assert len(cal["posts"]) == 150
    # media list: usage counted in one pass, paged
    md = c.get("/api/media?limit=10").get_json()
    assert md["media"][0]["used"] == 150 and md["total"] == 1


def test_status_has_a_change_counter(c):
    r1 = c.get("/api/status").get_json()["rev"]
    r2 = c.get("/api/status").get_json()["rev"]
    connect(c)
    r3 = c.get("/api/status").get_json()["rev"]
    assert r1 == r2 and r3 > r2, "the UI redraws only when something changed"
    q = c.get("/api/status").get_json()["queue"]
    assert set(q) == {"workers", "busy", "videos"}


def test_free_slots_fast_for_many():
    t0 = time.time()
    s = engine_mod.free_slots([], 500, per_day=3)
    assert len(s) == 500 and len(set(s)) == 500 and s == sorted(s)
    assert time.time() - t0 < 2


# ------------------------------------------------------------------ resumable YouTube upload
def test_youtube_upload_resumes_after_a_dropped_connection(fake, monkeypatch):
    monkeypatch.setattr("connectors.more.CHUNK", 1024 * 256)
    monkeypatch.setattr("time.sleep", lambda s: None)
    size = 1024 * 256 * 3 + 100
    p = os.path.join(tempfile.mkdtemp(), "v.mp4")
    with open(p, "wb") as f:
        f.write(os.urandom(size))
    calls = {"n": 0}

    def answer(kw):
        calls["n"] += 1
        return {"id": "VID"} if calls["n"] >= 5 else {}
    # piece 1 ok (308), piece 2 -> server error, status says Google has piece 1, piece 2 again, piece 3, last piece -> done
    fake.on("PUT", "upload.example/s", status=308, headers={"Range": "bytes=0-262143"}, times=1)
    fake.on("PUT", "upload.example/s", status=503, body={"error": "busy"}, times=1)
    fake.on("PUT", "upload.example/s", status=308, headers={"Range": "bytes=0-262143"}, times=1)          # status query
    fake.on("PUT", "upload.example/s", status=308, headers={"Range": "bytes=0-524287"}, times=1)
    fake.on("PUT", "upload.example/s", status=308, headers={"Range": "bytes=0-786431"}, times=1)
    fake.on("PUT", "upload.example/s", status=200, body={"id": "VID"})
    d = resumable_upload("https://upload.example/s", p, "video/mp4", size)
    assert d["id"] == "VID"
    puts = fake.find("upload.example/s", "PUT")
    ranges = [c["headers"].get("Content-Range") for c in puts]
    assert ranges[0] == "bytes 0-262143/%d" % size
    assert "bytes */%d" % size in ranges, "asked Google where it stopped"
    assert ranges.count("bytes 262144-524287/%d" % size) == 2, "only the lost piece is sent again — no restart from zero"
    assert ranges[-1] == "bytes 786432-%d/%d" % (size - 1, size)


def test_youtube_upload_gives_up_on_a_real_error(fake, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    p = os.path.join(tempfile.mkdtemp(), "v.mp4")
    with open(p, "wb") as f:
        f.write(b"x" * 1000)
    fake.on("PUT", "upload.example/s", status=400, body={"error": {"message": "bad video"}})
    with pytest.raises(PublishError) as e:
        resumable_upload("https://upload.example/s", p, "video/mp4", 1000)
    assert e.value.kind == "invalid"
