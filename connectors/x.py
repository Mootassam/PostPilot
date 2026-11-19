"""X (Twitter) — API v2 with OAuth 1.0a user keys (works on the free tier: posting + media upload)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import quote, urlparse, parse_qsl

import net
from connectors.base import Connector, PublishError, item, split_thread, ts_of

API = "https://api.x.com/2"
UPLOAD_V1 = "https://upload.twitter.com/1.1/media/upload.json"


def _pct(s) -> str:
    return quote(str(s), safe="~-._")


def oauth1_header(method: str, url: str, creds: dict, params: dict | None = None, nonce: str = "", ts: str = "") -> str:
    """RFC 5849 HMAC-SHA1 signature. `params` = query/form params that are part of the signature
    (JSON and multipart bodies are not signed)."""
    oauth = {"oauth_consumer_key": creds["api_key"], "oauth_nonce": nonce or secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": ts or str(int(time.time())),
             "oauth_token": creds["access_token"], "oauth_version": "1.0"}
    p = urlparse(url)
    allp = list(parse_qsl(p.query, keep_blank_values=True)) + list((params or {}).items()) + list(oauth.items())
    norm = "&".join(f"{k}={v}" for k, v in sorted((_pct(k), _pct(v)) for k, v in allp))
    base_url = f"{p.scheme}://{p.netloc}{p.path}"
    base = "&".join([method.upper(), _pct(base_url), _pct(norm)])
    key = f"{_pct(creds['api_secret'])}&{_pct(creds['access_secret'])}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


class X(Connector):
    key, name, color, abbr = "x", "X (Twitter)", "#000000", "X"
    text_limit = 280
    max_images = 4
    supports = ("thread",)
    connect = "form"
    form_fields = [{"key": "api_key", "label": "API Key (Consumer key)"},
                   {"key": "api_secret", "label": "API Key Secret", "secret": True},
                   {"key": "access_token", "label": "Access Token"},
                   {"key": "access_secret", "label": "Access Token Secret", "secret": True}]
    guide = [
        "Go to <a href='https://developer.x.com/en/portal/dashboard' target='_blank'>developer.x.com</a> and sign up (the <b>Free</b> plan allows posting).",
        "Open your Project's App → <b>User authentication settings</b> → <b>Set up</b>: App permissions <b>Read and write</b>, type <b>Web App / Automated App</b>, any website & callback (e.g. <code>{redirect}</code>).",
        "Go to <b>Keys and tokens</b>: copy the <b>API Key</b> and <b>Secret</b>, then <b>generate</b> the <b>Access Token and Secret</b> (after setting Read and write!).",
        "Paste the four values below.",
    ]
    note = "The X Free plan allows a limited number of posts per month — PostPilot shows the error if you reach it."
    inbox = ("mentions",)
    inbox_note = "Mentions and replies need the X Basic plan (reading is not included in the Free plan)."

    def check(self, account, creds):
        url = f"{API}/users/me"
        try:
            net.get_json(url, headers=self._auth("GET", url, creds))
        except PublishError as e:
            if e.status == 401:
                raise PublishError("X rejected the keys — they were regenerated or the app was suspended. Paste new keys.", "auth", 401) from None
            if e.kind == "rate":
                return {}  # the check itself is rate limited on the Free plan — that is not a problem
            raise
        return {}

    def inbox_fetch(self, account, creds, state, posts):
        url = f"{API}/users/{account['external_id']}/mentions"
        q = {"max_results": "50", "tweet.fields": "created_at,conversation_id,author_id", "expansions": "author_id",
             "user.fields": "username,name,profile_image_url"}
        if state.get("since"):
            q["since_id"] = state["since"]
        try:
            d = net.get_json(url, params=q, headers=self._auth("GET", url, creds, q))
        except PublishError as e:
            if e.status in (401, 403) or "client-not-enrolled" in str(e).lower():
                raise PublishError("Reading mentions isn't included in your X plan (Basic or higher is needed).", "plan", e.status) from None
            raise
        users = {u["id"]: u for u in (d.get("includes") or {}).get("users", [])}
        ours = {p["remote_id"]: p for p in posts}
        out = []
        for t in d.get("data", []):
            u = users.get(t.get("author_id"), {})
            post = ours.get(t.get("conversation_id"))
            out.append(item(t["id"], "reply" if post else "mention", t.get("text", ""), reply_to=t["id"], thread_key=t.get("conversation_id", ""),
                            post=post, author_id=t.get("author_id", ""), author_name=u.get("name", ""), author_handle="@" + u.get("username", ""),
                            author_avatar=u.get("profile_image_url", ""), url=f"https://x.com/{u.get('username', 'i')}/status/{t['id']}",
                            created_at=ts_of(t.get("created_at")), mine=t.get("author_id") == account["external_id"]))
        newest = (d.get("meta") or {}).get("newest_id")
        return out, ({**state, "since": newest} if newest else state)

    def inbox_reply(self, account, creds, it, text):
        url = f"{API}/tweets"
        d = net.post_json(url, headers=self._auth("POST", url, creds), json={"text": text, "reply": {"in_reply_to_tweet_id": it["reply_to"]}})
        return {"remote_id": d["data"]["id"]}

    def _auth(self, method, url, creds, params=None):
        return {"Authorization": oauth1_header(method, url, creds, params)}

    def connect_form(self, data):
        creds = {k: (data.get(k) or "").strip() for k in ("api_key", "api_secret", "access_token", "access_secret")}
        if not all(creds.values()):
            raise PublishError("All four keys are required.")
        url = f"{API}/users/me"
        q = {"user.fields": "profile_image_url,username,name"}
        try:
            me = net.get_json(url, params=q, headers=self._auth("GET", url, creds, q))["data"]
        except PublishError as e:
            if e.status in (401, 403):
                raise PublishError("X rejected these keys — check them, and make sure the app has “Read and write” permission "
                                   "and that you regenerated the Access Token after changing it.") from None
            raise
        return [{"network": "x", "name": me.get("name"), "handle": "@" + me.get("username", ""), "external_id": me["id"],
                 "avatar": me.get("profile_image_url", ""), "creds": creds, "extra": {"username": me.get("username", "")}}]

    # --- media
    def _upload(self, creds, m) -> str:
        size = os.path.getsize(m["path"])
        if m["kind"] == "image" and size <= 5_000_000:
            url = f"{API}/media/upload"
            try:
                with open(m["path"], "rb") as f:
                    d = net.post_json(url, headers=self._auth("POST", url, creds), data={"media_category": "tweet_image"},
                                      files={"media": (m["name"], f, m["mime"])})
                mid = (d.get("data") or {}).get("id") or d.get("media_id_string")
            except PublishError as e:
                if e.status not in (403, 404):
                    raise
                with open(m["path"], "rb") as f:  # older v1.1 endpoint as a fallback
                    d = net.post_json(UPLOAD_V1, headers=self._auth("POST", UPLOAD_V1, creds), files={"media": (m["name"], f, m["mime"])})
                mid = d["media_id_string"]
            self._alt(creds, mid, m.get("alt"))
            return mid
        return self._upload_chunked(creds, m, size)

    def _upload_chunked(self, creds, m, size) -> str:
        cat = "tweet_video" if m["kind"] == "video" else ("tweet_gif" if m["kind"] == "gif" else "tweet_image")
        url = f"{API}/media/upload/initialize"
        d = net.post_json(url, headers=self._auth("POST", url, creds),
                          json={"media_type": m["mime"], "total_bytes": size, "media_category": cat})
        mid = (d.get("data") or {}).get("id") or d.get("media_id_string")
        with open(m["path"], "rb") as f:
            i = 0
            while True:
                chunk = f.read(4 * 1024 * 1024)
                if not chunk:
                    break
                url = f"{API}/media/upload/{mid}/append"
                net.request("POST", url, headers=self._auth("POST", url, creds), data={"segment_index": str(i)},
                            files={"media": ("chunk", chunk, "application/octet-stream")})
                i += 1
        url = f"{API}/media/upload/{mid}/finalize"
        d = net.post_json(url, headers=self._auth("POST", url, creds))
        info = (d.get("data") or {}).get("processing_info") or d.get("processing_info")
        while info and info.get("state") in ("pending", "in_progress"):
            time.sleep(min(10, int(info.get("check_after_secs", 3))))
            url = f"{API}/media/upload"
            q = {"command": "STATUS", "media_id": mid}
            d = net.get_json(url, params=q, headers=self._auth("GET", url, creds, q))
            info = (d.get("data") or {}).get("processing_info") or d.get("processing_info")
        if info and info.get("state") == "failed":
            raise PublishError(f"X could not process the video: {(info.get('error') or {}).get('message', 'unknown error')}")
        return mid

    def _alt(self, creds, mid, alt):
        if not alt:
            return
        url = f"{API}/media/metadata"
        try:
            net.request("POST", url, headers=self._auth("POST", url, creds), json={"id": mid, "metadata": {"alt_text": {"text": alt[:1000]}}})
        except PublishError:
            pass

    def publish(self, account, creds, post):
        media_ids = [self._upload(creds, m) for m in post["media"]]
        parts = split_thread(post["text"], self.text_limit, "x") if post["options"].get("thread", True) else [post["text"]]
        url = f"{API}/tweets"
        first = prev = None
        rate = {}
        for i, part in enumerate(parts):
            body = {"text": part}
            if i == 0 and media_ids:
                body["media"] = {"media_ids": media_ids}
            if prev:
                body["reply"] = {"in_reply_to_tweet_id": prev}
            try:
                r = net.request("POST", url, headers=self._auth("POST", url, creds), json=body)
                d = r.json()
                rem, reset = r.headers.get("x-user-limit-24hour-remaining"), r.headers.get("x-user-limit-24hour-reset")
                if rem is not None and str(rem).isdigit():
                    rate = {"remaining": int(rem), "reset": int(reset) if reset and str(reset).isdigit() else 0,
                            "label": "X says your 24-hour posting limit is used up"}
            except PublishError as e:
                if e.status == 429:
                    e.retry_after = e.retry_after or 3600
                if e.status == 403 and "duplicate" in str(e).lower():
                    raise PublishError("X refused a duplicate post — change the text a little.") from None
                if e.status == 403:
                    e.kind = "invalid"
                if first:  # part of the thread is already live — never repost it, report what happened
                    return {"remote_id": first, "url": f"https://x.com/{account.get('handle', '').lstrip('@') or 'i'}/status/{first}",
                            "units": i, "rate": rate, "warning": f"Only {i} of {len(parts)} thread parts were posted: {e}"}
                raise
            prev = d["data"]["id"]
            first = first or prev
        uname = account.get("handle", "").lstrip("@") or "i"
        return {"remote_id": first, "url": f"https://x.com/{uname}/status/{first}", "units": len(parts), "rate": rate}

    def metrics(self, account, creds, remote_id):
        url = f"{API}/tweets/{remote_id}"
        q = {"tweet.fields": "public_metrics"}
        m = net.get_json(url, params=q, headers=self._auth("GET", url, creds, q))["data"]["public_metrics"]
        return {"likes": m.get("like_count", 0), "comments": m.get("reply_count", 0),
                "shares": m.get("retweet_count", 0) + m.get("quote_count", 0), "views": m.get("impression_count")}
