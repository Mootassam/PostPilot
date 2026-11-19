"""Each connector against a fake network: the requests must match the official API shapes."""

import json
import os
import tempfile

import pytest
from PIL import Image

from connectors import base
from connectors.linkedin import LinkedIn, escape
from connectors.meta import Facebook, Instagram
from connectors.more import Pinterest, YouTube
from connectors.simple import Bluesky, Discord, Mastodon, Telegram, facets
from connectors.threads import Threads
from connectors.x import X, oauth1_header
from net import PublishError


def img(w=800, h=600, name="pic.jpg"):
    p = os.path.join(tempfile.mkdtemp(), name)
    Image.new("RGB", (w, h), (200, 30, 30)).save(p)
    return {"id": 1, "path": p, "kind": "image", "mime": "image/jpeg", "name": name, "alt": "red square", "width": w, "height": h}


def vid():
    p = os.path.join(tempfile.mkdtemp(), "clip.mp4")
    with open(p, "wb") as f:
        f.write(b"\x00" * 3000)
    return {"id": 2, "path": p, "kind": "video", "mime": "video/mp4", "name": "clip.mp4", "alt": "", "width": None, "height": None}


def post(text="Hello world #launch https://example.com", media=(), link="", **opts):
    return {"text": text, "text_no_link": text, "link": link, "media": list(media), "options": opts, "first_comment": opts.pop("first_comment", ""),
            "public_url": lambda m: f"https://pub.example/m/{m['name']}", "target_id": 7, "post_id": 3}


ACC = {"id": 1, "external_id": "123", "handle": "@me", "extra_d": {}}


# ------------------------------------------------------------------ text helpers
def test_x_length_counts_links_as_23():
    assert base.length("hi https://example.com/a/very/long/path/that/goes/on/and/on", "x") == 3 + 23


def test_split_thread_numbers_parts():
    text = " ".join(f"Sentence number {i} is here." for i in range(40))
    parts = base.split_thread(text, 280, "x")
    assert len(parts) > 1 and all(base.length(p, "x") <= 280 for p in parts)
    assert parts[0].endswith(f"1/{len(parts)}") and parts[-1].endswith(f"{len(parts)}/{len(parts)}")


def test_utm():
    u = base.add_utm("https://shop.com/p?x=1", "facebook", "{network}", "social", "spring")
    assert "utm_source=facebook" in u and "utm_medium=social" in u and "x=1" in u
    assert "utm_source=keep" in base.add_utm("https://a.com?utm_source=keep", "x", "{network}", "s", "c")


def test_oauth1_matches_twitter_reference_example():
    creds = {"api_key": "xvz1evFS4wEEPTGEFPHBog", "api_secret": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
             "access_token": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "access_secret": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"}
    h = oauth1_header("POST", "https://api.twitter.com/1.1/statuses/update.json?include_entities=true", creds,
                      {"status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
                      nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", ts="1318622958")
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in h


def test_linkedin_escaping_and_hashtags():
    out = escape("Big news (finally) for @everyone_here #Launch2026!")
    assert "\\(finally\\)" in out and "\\@everyone\\_here" in out and "{hashtag|\\#|Launch2026}" in out


def test_bluesky_facets_use_byte_offsets():
    t = "Café ☕ news https://ex.com #Coffee"
    fc = facets(t)
    b = t.encode()
    link = next(f for f in fc if f["features"][0]["$type"].endswith("#link"))
    assert b[link["index"]["byteStart"]:link["index"]["byteEnd"]] == b"https://ex.com"
    tag = next(f for f in fc if f["features"][0]["$type"].endswith("#tag"))
    assert b[tag["index"]["byteStart"]:tag["index"]["byteEnd"]] == b"#Coffee" and tag["features"][0]["tag"] == "Coffee"


# ------------------------------------------------------------------ Facebook / Instagram
def test_facebook_text_with_link_and_first_comment(fake):
    fake.on("POST", "/123/feed", body={"id": "123_9"}).on("GET", "/123_9", body={"permalink_url": "https://fb.com/p/9"})
    fake.on("POST", "/123_9/comments", body={"id": "c1"})
    r = Facebook().publish(ACC, {"token": "PT"}, post("Hello", link="https://shop.com", first_comment="Link in bio!"))
    feed = fake.find("/123/feed")[0]
    assert feed["data"]["message"] == "Hello" and feed["data"]["link"] == "https://shop.com" and feed["data"]["access_token"] == "PT"
    assert fake.find("/comments")[0]["data"]["message"] == "Link in bio!"
    assert r == {"remote_id": "123_9", "url": "https://fb.com/p/9"}


def test_facebook_multi_photo_uses_unpublished_uploads(fake):
    n = iter(range(100))
    fake.on("POST", "/123/photos", body=lambda kw: {"id": f"ph{next(n)}"})
    fake.on("POST", "/123/feed", body={"id": "123_5"}).on("GET", "/123_5", body={"permalink_url": "u"})
    Facebook().publish(ACC, {"token": "PT"}, post("Album", media=[img(), img()]))
    ups = fake.find("/123/photos")
    assert len(ups) == 2 and all(u["data"]["published"] == "false" for u in ups)
    feed = fake.find("/123/feed")[0]["data"]
    assert json.loads(feed["attached_media[1]"])["media_fbid"] == "ph1"


def test_instagram_single_image(fake):
    fake.on("POST", "/123/media_publish", body={"id": "m9"}).on("POST", "/123/media", body={"id": "c1"})
    fake.on("GET", "/c1", body={"status_code": "FINISHED"}).on("GET", "/m9", body={"permalink": "https://instagram.com/p/x"})
    r = Instagram().publish(ACC, {"token": "PT"}, post("Caption #a", media=[img()]))
    cont = fake.find("/123/media", "POST")[0]["data"]
    assert cont["image_url"] == "https://pub.example/m/pic.jpg" and cont["caption"] == "Caption #a" and cont["alt_text"] == "red square"
    assert fake.find("media_publish")[0]["data"]["creation_id"] == "c1"
    assert r["url"] == "https://instagram.com/p/x"


def test_instagram_carousel_and_processing_error(fake):
    ids = iter(["k1", "k2", "car"])
    fake.on("POST", "/123/media_publish", body={"id": "m1"}).on("POST", "/123/media", body=lambda kw: {"id": next(ids)})
    fake.on("GET", "/k1", body={"status_code": "FINISHED"}).on("GET", "/k2", body={"status_code": "FINISHED"})
    fake.on("GET", "/car", body={"status_code": "FINISHED"}).on("GET", "/m1", body={"permalink": "p"})
    Instagram().publish(ACC, {"token": "PT"}, post("C", media=[img(), vid()]))
    calls = fake.find("/123/media", "POST")
    assert calls[1]["data"]["media_type"] == "VIDEO" and calls[2]["data"]["media_type"] == "CAROUSEL" and calls[2]["data"]["children"] == "k1,k2"
    fake2 = type(fake)()
    import net
    net.SESSION = fake2
    fake2.on("POST", "/123/media", body={"id": "bad"}).on("GET", "/bad", body={"status_code": "ERROR", "status": "Unsupported format"})
    with pytest.raises(PublishError, match="Unsupported format"):
        Instagram().publish(ACC, {"token": "PT"}, post("C", media=[img()]))


def test_instagram_requires_media_and_hashtag_limit():
    errs = Instagram().validate({"text": "x " + " ".join(f"#t{i}" for i in range(31)), "media": [], "options": {}})
    assert any("needs a photo" in e for e in errs) and any("30 hashtags" in e for e in errs)


# ------------------------------------------------------------------ Threads / LinkedIn / X
def test_threads_auto_thread(fake):
    ids = iter(["a", "b", "c", "d", "e", "f"])
    fake.on("POST", "/threads_publish", body=lambda kw: {"id": "pub-" + kw["data"]["creation_id"]})
    fake.on("POST", "/123/threads", body=lambda kw: {"id": next(ids)}).on("GET", "/pub-a", body={"permalink": "https://threads.net/@me/post/1"})
    long = " ".join(f"Line {i} of my long story." for i in range(40))
    r = Threads().publish(ACC, {"token": "T"}, post(long))
    creates = [c for c in fake.find("/123/threads", "POST") if "threads_publish" not in c["url"]]
    assert len(creates) >= 2 and creates[1]["data"]["reply_to_id"] == "pub-a"
    assert all(len(c["data"]["text"]) <= 500 for c in creates) and r["url"].startswith("https://threads.net")


def test_linkedin_image_post(fake):
    fake.on("POST", "/rest/images?action=initializeUpload", body={"value": {"uploadUrl": "https://up.linkedin/x", "image": "urn:li:image:9"}})
    fake.on("PUT", "https://up.linkedin/x", status=201)
    fake.on("POST", "/rest/posts", status=201, headers={"x-restli-id": "urn:li:share:77"})
    r = LinkedIn().publish(ACC, {"token": "L"}, post("Hi (all) #News", media=[img()]))
    p = fake.find("/rest/posts")[0]
    assert p["json"]["author"] == "urn:li:person:123" and p["json"]["content"]["media"]["id"] == "urn:li:image:9"
    assert "{hashtag|\\#|News}" in p["json"]["commentary"] and p["headers"]["LinkedIn-Version"].isdigit()
    assert r["url"] == "https://www.linkedin.com/feed/update/urn:li:share:77/"


def test_linkedin_link_article(fake):
    fake.on("POST", "/rest/posts", status=201, headers={"x-restli-id": "urn:li:share:1"})
    LinkedIn().publish(ACC, {"token": "L"}, post("Read this", link="https://blog.com/a"))
    assert fake.find("/rest/posts")[0]["json"]["content"]["article"]["source"] == "https://blog.com/a"


def test_x_tweet_with_image_and_thread(fake):
    n = iter(range(1, 50))
    fake.on("POST", "/2/media/upload", body={"data": {"id": "M1"}}).on("POST", "/2/media/metadata", body={})
    fake.on("POST", "/2/tweets", status=201, body=lambda kw: {"data": {"id": f"T{next(n)}"}})
    creds = {"api_key": "a", "api_secret": "b", "access_token": "c", "access_secret": "d"}
    long = " ".join(f"Point {i} matters a lot." for i in range(30))
    r = X().publish(ACC, creds, post(long, media=[img()]))
    tweets = fake.find("/2/tweets")
    assert tweets[0]["json"]["media"]["media_ids"] == ["M1"] and tweets[1]["json"]["reply"]["in_reply_to_tweet_id"] == "T1"
    assert tweets[0]["headers"]["Authorization"].startswith("OAuth ") and r["url"] == "https://x.com/me/status/T1"


def test_x_falls_back_to_v1_upload(fake):
    fake.on("POST", "/2/media/upload", status=404, body={"title": "Not Found"})
    fake.on("POST", "upload.twitter.com", body={"media_id_string": "OLD1"}).on("POST", "/2/media/metadata", body={})
    fake.on("POST", "/2/tweets", status=201, body={"data": {"id": "T9"}})
    X().publish(ACC, {"api_key": "a", "api_secret": "b", "access_token": "c", "access_secret": "d"}, post("hi", media=[img()]))
    assert fake.find("/2/tweets")[0]["json"]["media"]["media_ids"] == ["OLD1"]


def test_x_auth_error_is_auth_kind(fake):
    fake.on("POST", "/2/tweets", status=401, body={"title": "Unauthorized", "detail": "Unauthorized"})
    with pytest.raises(PublishError) as e:
        X().publish(ACC, {"api_key": "a", "api_secret": "b", "access_token": "c", "access_secret": "d"}, post("hi"))
    assert e.value.kind == "auth"


# ------------------------------------------------------------------ Telegram / Discord / Mastodon / Bluesky
def test_telegram_album_with_long_caption(fake):
    fake.on("POST", "sendMediaGroup", body={"ok": True, "result": [{"message_id": 50}, {"message_id": 51}]})
    fake.on("POST", "sendMessage", body={"ok": True, "result": {"message_id": 52}})
    acc = {**ACC, "external_id": "-1001", "extra_d": {"username": "mychan"}}
    r = Telegram().publish(acc, {"bot_token": "B"}, post("x" * 1500, media=[img(), img()]))
    grp = json.loads(fake.find("sendMediaGroup")[0]["data"]["media"])
    assert len(grp) == 2 and "caption" not in grp[0] and fake.find("sendMessage")
    assert r["url"] == "https://t.me/mychan/50"


def test_telegram_rate_limit(fake):
    fake.on("POST", "sendMessage", status=429, body={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 33}})
    with pytest.raises(PublishError) as e:
        Telegram().publish({**ACC, "extra_d": {}}, {"bot_token": "B"}, post("hi"))
    assert e.value.kind == "rate" and e.value.retry_after == 33


def test_discord_webhook(fake):
    fake.on("POST", "discord.com/api/webhooks", body={"id": "99", "channel_id": "5"})
    r = Discord().publish({**ACC, "extra_d": {"guild_id": "1"}}, {"webhook": "https://discord.com/api/webhooks/1/abc"}, post("hi", media=[img()]))
    c = fake.find("webhooks")[0]
    assert json.loads(c["data"]["payload_json"])["content"] == "hi" and "files[0]" in c["files"] and r["url"].endswith("/1/5/99")


def test_mastodon_media_then_status(fake):
    fake.on("POST", "/api/v2/media", status=202, body={"id": "m1", "url": None}).on("GET", "/api/v1/media/m1", body={"id": "m1", "url": "u"})
    fake.on("POST", "/api/v1/statuses", body={"id": "s1", "url": "https://mastodon.social/@me/s1"})
    r = Mastodon().publish(ACC, {"instance": "mastodon.social", "token": "T"}, post("hello", media=[img()]))
    st = fake.find("/api/v1/statuses")[0]
    assert st["json"]["media_ids"] == ["m1"] and st["headers"]["Idempotency-Key"] and r["remote_id"] == "s1"


def test_bluesky_post_with_image(fake):
    fake.on("POST", "createSession", body={"accessJwt": "J", "did": "did:plc:me", "handle": "me.bsky.social",
                                           "didDoc": {"service": [{"id": "#atproto_pds", "serviceEndpoint": "https://pds.example"}]}})
    fake.on("POST", "uploadBlob", body={"blob": {"$type": "blob", "ref": {"$link": "x"}, "mimeType": "image/jpeg", "size": 10}})
    fake.on("POST", "createRecord", body={"uri": "at://did:plc:me/app.bsky.feed.post/3abc", "cid": "c"})
    r = Bluesky().publish(ACC, {"handle": "me", "password": "p"}, post("Hello #bsky", media=[img()]))
    rec = fake.find("createRecord")[0]
    assert rec["url"].startswith("https://pds.example") and rec["json"]["record"]["embed"]["images"][0]["alt"] == "red square"
    assert r["url"] == "https://bsky.app/profile/me.bsky.social/post/3abc"


# ------------------------------------------------------------------ Pinterest / YouTube
def test_pinterest_pin(fake):
    fake.on("POST", "/v5/pins", status=201, body={"id": "p1"})
    r = Pinterest().publish({**ACC, "extra_d": {"board_id": "b1"}}, {"token": "T"}, post("Nice", media=[img()], link="https://s.com", title="Title"))
    body = fake.find("/v5/pins")[0]["json"]
    assert body["board_id"] == "b1" and body["media_source"]["source_type"] == "image_base64" and body["link"] == "https://s.com"
    assert r["url"] == "https://www.pinterest.com/pin/p1/"


def test_youtube_resumable_upload(fake, monkeypatch):
    fake.on("POST", "upload/youtube/v3/videos", headers={"Location": "https://upload.example/session1"})
    fake.on("PUT", "https://upload.example/session1", body={"id": "vid1"})
    r = YouTube().publish(ACC, {"token": "T", "token_exp": 9999999999}, post("My video\nmore #tag", media=[vid()], title="Launch", privacy="unlisted"))
    meta = fake.find("upload/youtube")[0]["json"]
    assert meta["snippet"]["title"] == "Launch" and meta["status"]["privacyStatus"] == "unlisted" and meta["snippet"]["tags"] == ["tag"]
    assert r["url"] == "https://youtu.be/vid1"


def test_error_mapping(fake):
    fake.on("POST", "/123/feed", status=500, body={"error": {"message": "Service temporarily unavailable"}})
    with pytest.raises(PublishError) as e:
        Facebook().publish(ACC, {"token": "PT"}, post("x"))
    assert e.value.kind == "transient" and "temporarily" in str(e.value)


def test_youtube_native_schedule_sets_publish_at(fake):
    fake.on("POST", "upload/youtube/v3/videos", headers={"Location": "https://upload.example/s2"})
    fake.on("PUT", "https://upload.example/s2", body={"id": "vid2"})
    p = post("Hi #x", media=[vid()], title="Later")
    p["publish_at"] = 1795600000
    YouTube().publish(ACC, {"token": "T", "token_exp": 9999999999}, p)
    st = fake.find("upload/youtube")[0]["json"]["status"]
    assert st["privacyStatus"] == "private" and st["publishAt"].endswith("Z") and st["publishAt"].startswith("2026-")
    assert YouTube().can_native(post("x", privacy="public")) and not YouTube().can_native(post("x", privacy="unlisted"))


def test_facebook_native_schedule(fake):
    fake.on("POST", "/123/feed", body={"id": "123_9"})
    fake.on("GET", "/123_9", body={"permalink_url": "https://facebook.com/p/9"})
    p = post("Soon")
    p["publish_at"] = 1795600000
    Facebook().publish(ACC, {"token": "PT"}, p)
    d = fake.find("/123/feed")[0]["data"]
    assert d["published"] == "false" and d["scheduled_publish_time"] == "1795600000"
    assert not Facebook().can_native(post("x", first_comment="hi")) and Facebook().can_native(post("x"))
