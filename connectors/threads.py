"""Threads (Meta) — official Threads API. Long-lived tokens last 60 days and are refreshed automatically."""

from __future__ import annotations

import time

import net
from connectors.base import Connector, PublishError, item, split_thread, ts_of

API = "https://graph.threads.net/v1.0"


class Threads(Connector):
    key, name, color, abbr = "threads", "Threads", "#101010", "@"
    text_limit = 500
    max_images = 10
    allows_mixed = True
    supports = ("thread",)
    connect = "form"
    form_fields = [{"key": "token", "label": "Threads long-lived access token", "secret": True,
                    "help": "Meta app → Use cases → Access the Threads API → Settings → User Token Generator"}]
    guide = [
        "Go to <a href='https://developers.facebook.com/apps' target='_blank'>developers.facebook.com/apps</a> → <b>Create app</b> → use case <b>Access the Threads API</b>.",
        "Under <b>Use cases → Customize → Permissions</b> add <b>threads_basic</b>, <b>threads_content_publish</b> and <b>threads_manage_insights</b>.",
        "Under <b>App roles → Roles</b> add your Threads account as a <b>Threads Tester</b> and accept the invite in Threads (Settings → Account → Website permissions).",
        "In <b>Use cases → Settings</b> use the <b>User Token Generator</b> to create a token and paste it below.",
    ]
    note = "Threads downloads images from a temporary secure link that PostPilot opens while posting."
    inbox = ("comments",)
    inbox_note = "Replies to your Threads posts (needs the threads_read_replies and threads_manage_replies permissions on your token)."

    def check(self, account, creds):
        net.get_json(f"{API}/me", params={"fields": "id,username", "access_token": creds["token"]})
        return {}

    def inbox_fetch(self, account, creds, state, posts):
        me = (account.get("extra_d") or {}).get("username", "")
        out = []
        for p in posts:
            d = net.get_json(f"{API}/{p['remote_id']}/conversation", params={
                "fields": "id,text,username,timestamp,permalink", "reverse": "false", "access_token": creds["token"]})
            for c in d.get("data", []):
                out.append(item(c["id"], "reply", c.get("text", ""), reply_to=c["id"], thread_key=p["remote_id"], post=p,
                                author_handle="@" + c.get("username", ""), author_name=c.get("username", ""), url=c.get("permalink", ""),
                                created_at=ts_of(c.get("timestamp")), mine=c.get("username") == me))
        return out, state

    def inbox_reply(self, account, creds, it, text):
        rid = self._create(account["external_id"], creds["token"], text, [], reply_to=it["reply_to"])
        return {"remote_id": rid}

    def connect_form(self, data):
        tok = (data.get("token") or "").strip()
        if not tok:
            raise PublishError("Paste your Threads access token.")
        me = net.get_json(f"{API}/me", params={"fields": "id,username,name,threads_profile_picture_url", "access_token": tok})
        exp = None
        try:  # make sure it is a long-lived token (60 days)
            r = net.get_json(f"{API}/refresh_access_token", params={"grant_type": "th_refresh_token", "access_token": tok})
            tok, exp = r.get("access_token", tok), int(time.time()) + int(r.get("expires_in", 0) or 0) or None
        except PublishError:
            pass
        return [{"network": "threads", "name": me.get("name") or me.get("username"), "handle": "@" + me.get("username", ""),
                 "external_id": me["id"], "avatar": me.get("threads_profile_picture_url", ""), "creds": {"token": tok},
                 "expires_at": exp, "extra": {"username": me.get("username", "")}}]

    def refresh(self, account, creds, app):
        r = net.get_json(f"{API}/refresh_access_token", params={"grant_type": "th_refresh_token", "access_token": creds["token"]})
        return {"token": r["access_token"], "expires_at": int(time.time()) + int(r.get("expires_in", 5_000_000))}

    def _wait(self, cid, token):
        for _ in range(60):
            st = net.get_json(f"{API}/{cid}", params={"fields": "status,error_message", "access_token": token})
            if st.get("status") == "FINISHED":
                return
            if st.get("status") in ("ERROR", "EXPIRED"):
                raise PublishError(f"Threads could not process the media: {st.get('error_message') or st.get('status')}")
            time.sleep(3)
        raise PublishError("Threads took too long to process the media — will retry.", "transient")

    def _create(self, uid, token, text, media, reply_to=None, public_url=None):
        base = {"access_token": token, **({"reply_to_id": reply_to} if reply_to else {})}
        if not media:
            cid = net.post_json(f"{API}/{uid}/threads", data={**base, "media_type": "TEXT", "text": text})["id"]
        elif len(media) == 1:
            m = media[0]
            kind = "VIDEO" if m["kind"] == "video" else "IMAGE"
            key = "video_url" if kind == "VIDEO" else "image_url"
            cid = net.post_json(f"{API}/{uid}/threads", data={**base, "media_type": kind, key: public_url(m), "text": text,
                                                              **({"alt_text": m["alt"]} if m.get("alt") else {})})["id"]
            self._wait(cid, token)
        else:
            kids = []
            for m in media[:10]:
                kind = "VIDEO" if m["kind"] == "video" else "IMAGE"
                key = "video_url" if kind == "VIDEO" else "image_url"
                kids.append(net.post_json(f"{API}/{uid}/threads", data={"access_token": token, "media_type": kind, key: public_url(m),
                                                                        "is_carousel_item": "true"})["id"])
            for k in kids:
                self._wait(k, token)
            cid = net.post_json(f"{API}/{uid}/threads", data={**base, "media_type": "CAROUSEL", "children": ",".join(kids), "text": text})["id"]
            self._wait(cid, token)
        return net.post_json(f"{API}/{uid}/threads_publish", data={"creation_id": cid, "access_token": token})["id"]

    def publish(self, account, creds, post):
        uid, token = account["external_id"], creds["token"]
        parts = split_thread(post["text"], self.text_limit, "threads")
        first = self._create(uid, token, parts[0], post["media"], public_url=post["public_url"])
        prev = first
        warning = ""
        for n, p in enumerate(parts[1:], start=1):
            time.sleep(2)
            try:
                prev = self._create(uid, token, p, [], reply_to=prev)
            except PublishError as e:  # the first post is live — report instead of reposting it
                warning = f"Only {n} of {len(parts)} thread parts were posted: {e}"
                parts = parts[:n]
                break
        try:
            url = net.get_json(f"{API}/{first}", params={"fields": "permalink", "access_token": token}).get("permalink", "")
        except PublishError:
            url = ""
        return {"remote_id": first, "url": url, "units": len(parts), "warning": warning}

    def metrics(self, account, creds, remote_id):
        d = net.get_json(f"{API}/{remote_id}/insights", params={"metric": "views,likes,replies,reposts,quotes", "access_token": creds["token"]})
        out = {}
        for m in d.get("data", []):
            v = (m.get("values") or [{}])[0].get("value", 0) if m.get("values") else (m.get("total_value") or {}).get("value", 0)
            out[{"replies": "comments", "reposts": "shares"}.get(m["name"], m["name"])] = v
        return out
