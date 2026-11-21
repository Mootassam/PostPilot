"""Facebook Pages + Instagram (Business/Creator) through the official Meta Graph API.

One Meta login discovers every Page you manage and the Instagram account linked to each Page.
Page tokens obtained from a long-lived user token do not expire.
"""

from __future__ import annotations

import time
from urllib.parse import urlencode

import net
from connectors.base import Connector, PublishError, item, ts_of

GRAPH = "https://graph.facebook.com/v23.0"
VIDEO_GRAPH = "https://graph-video.facebook.com/v23.0"
SCOPES = ",".join(["pages_show_list", "pages_read_engagement", "pages_manage_posts", "pages_manage_engagement",
                   "instagram_basic", "instagram_content_publish", "instagram_manage_comments",
                   "instagram_manage_insights", "business_management"])

APP_FIELDS = [{"key": "app_id", "label": "Meta App ID", "help": "developers.facebook.com → My Apps → your app → App settings → Basic"},
              {"key": "app_secret", "label": "Meta App secret", "secret": True, "help": "Same page, click “Show”"}]
GUIDE = [
    "Go to <a href='https://developers.facebook.com/apps' target='_blank'>developers.facebook.com/apps</a> → <b>Create app</b> → use case <b>“Other”</b> → type <b>Business</b>.",
    "Add the products <b>Facebook Login for Business</b> and <b>Instagram</b> (Instagram API with Facebook login).",
    "In <b>Facebook Login → Settings</b> add this Valid OAuth Redirect URI: <code>{redirect}</code>",
    "Copy the <b>App ID</b> and <b>App secret</b> (App settings → Basic) into the boxes below, then click <b>Log in with Meta</b>.",
    "Your Instagram must be a <b>Business or Creator</b> account linked to a Facebook Page.",
]


def _discover(user_token: str) -> list[dict]:
    """Every Page (and its linked Instagram account) the user manages."""
    out = []
    url = f"{GRAPH}/me/accounts"
    params = {"fields": "id,name,access_token,picture{url},instagram_business_account{id,username,name,profile_picture_url}",
              "limit": 100, "access_token": user_token}
    while url:
        d = net.get_json(url, params=params)
        for p in d.get("data", []):
            out.append({"network": "facebook", "name": p["name"], "handle": p["name"], "external_id": p["id"],
                        "avatar": ((p.get("picture") or {}).get("data") or {}).get("url", ""),
                        "creds": {"token": p["access_token"]}, "extra": {}})
            ig = p.get("instagram_business_account")
            if ig:
                out.append({"network": "instagram", "name": ig.get("name") or ig.get("username"), "handle": "@" + (ig.get("username") or ""),
                            "external_id": ig["id"], "avatar": ig.get("profile_picture_url", ""),
                            "creds": {"token": p["access_token"]}, "extra": {"page_id": p["id"], "username": ig.get("username", "")}})
        url = (d.get("paging") or {}).get("next")
        params = None
    if not out:
        raise PublishError("No Facebook Pages found. You must be an admin of at least one Page, and allow access to it in the login window.")
    return out


def _exchange_long(app: dict, token: str) -> str:
    if not (app.get("app_id") and app.get("app_secret")):
        return token
    d = net.get_json(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app["app_id"], "client_secret": app["app_secret"], "fb_exchange_token": token})
    return d.get("access_token") or token


class _Meta(Connector):
    connect = "both"
    app_fields = APP_FIELDS
    guide = GUIDE
    form_fields = [{"key": "user_token", "label": "User access token (alternative to Log in)", "secret": True,
                    "help": "From Graph API Explorer with the permissions pages_show_list, pages_manage_posts, instagram_content_publish…"}]

    def oauth_url(self, app, redirect, state):
        if not app.get("app_id"):
            raise PublishError("Enter your Meta App ID and secret first.")
        return "https://www.facebook.com/v23.0/dialog/oauth?" + urlencode(
            {"client_id": app["app_id"], "redirect_uri": redirect, "state": state, "scope": SCOPES, "response_type": "code"})

    def oauth_finish(self, app, redirect, code):
        d = net.get_json(f"{GRAPH}/oauth/access_token", params={
            "client_id": app["app_id"], "client_secret": app["app_secret"], "redirect_uri": redirect, "code": code})
        return _discover(_exchange_long(app, d["access_token"]))

    def connect_form(self, data):
        tok = (data.get("user_token") or "").strip()
        if not tok:
            raise PublishError("Paste a user access token, or use “Log in with Meta”.")
        from store import app_creds
        return _discover(_exchange_long(data.get("_app") or app_creds("meta"), tok))

    def _comment(self, obj_id: str, token: str, text: str) -> None:
        if text:
            try:
                net.post_json(f"{GRAPH}/{obj_id}/comments", data={"message": text, "access_token": token})
            except PublishError:
                pass  # the post itself is live — a failed first comment must not fail it


class Facebook(_Meta):
    key, name, color, abbr = "facebook", "Facebook Page", "#1877F2", "f"
    text_limit = 63206
    max_images = 10
    supports = ("first_comment", "link_field")
    inbox = ("comments",)
    inbox_note = "Comments on your Page posts. Messenger DMs need Meta app review, so they stay in Meta Business Suite."

    def check(self, account, creds):
        net.get_json(f"{GRAPH}/{account['external_id']}", params={"fields": "id,name", "access_token": creds["token"]})
        return {}

    def inbox_fetch(self, account, creds, state, posts):
        out = []
        for p in posts:
            d = net.get_json(f"{GRAPH}/{p['remote_id']}/comments", params={
                "filter": "stream", "order": "reverse_chronological", "limit": 50, "access_token": creds["token"],
                "fields": "id,message,created_time,from{id,name},parent{id},permalink_url"})
            for c in d.get("data", []):
                fr = c.get("from") or {}
                out.append(item(c["id"], "reply" if c.get("parent") else "comment", c.get("message", ""), reply_to=c["id"],
                                thread_key=p["remote_id"], post=p, author_id=fr.get("id", ""), author_name=fr.get("name") or "Facebook user",
                                url=c.get("permalink_url", ""), created_at=ts_of(c.get("created_time")), mine=fr.get("id") == account["external_id"]))
        return out, state

    def inbox_reply(self, account, creds, it, text):
        d = net.post_json(f"{GRAPH}/{it['reply_to']}/comments", data={"message": text, "access_token": creds["token"]})
        return {"remote_id": d.get("id", "")}

    # Page posts, photos and videos can be scheduled by Facebook itself (10 min … 30 days ahead)
    native_schedule = (20 * 60, 29 * 86400)

    def can_native(self, post):
        return not post.get("first_comment")  # a comment can't be added to a post that isn't live yet

    def publish(self, account, creds, post):
        page, token = account["external_id"], creds["token"]
        text, media = post["text_no_link"], post["media"]
        vids = [m for m in media if m["kind"] == "video"]
        imgs = [m for m in media if m["kind"] in ("image", "gif")]
        sched = {"published": "false", "scheduled_publish_time": str(int(post["publish_at"]))} if post.get("publish_at") else {}
        if vids:
            v = vids[0]
            with open(v["path"], "rb") as f:
                d = net.post_json(f"{VIDEO_GRAPH}/{page}/videos", data={"description": post["text"], "access_token": token, **sched},
                                  files={"source": (v["name"], f, v["mime"])}, timeout=(15, 1800))
            pid = d.get("id")
            url = f"https://www.facebook.com/{page}/videos/{pid}"
        elif len(imgs) == 1:
            with open(imgs[0]["path"], "rb") as f:
                d = net.post_json(f"{GRAPH}/{page}/photos", data={"message": post["text"], "access_token": token, **sched,
                                                                  **({"alt_text_custom": imgs[0]["alt"]} if imgs[0].get("alt") else {})},
                                  files={"source": (imgs[0]["name"], f, imgs[0]["mime"])})
            pid = d.get("post_id") or d.get("id")
            url = ""
        elif imgs:
            ids = []
            for m in imgs:
                with open(m["path"], "rb") as f:
                    r = net.post_json(f"{GRAPH}/{page}/photos", data={"published": "false", "access_token": token},
                                      files={"source": (m["name"], f, m["mime"])})
                ids.append(r["id"])
            data = {"message": post["text"], "access_token": token, **sched}
            for i, mid in enumerate(ids):
                data[f"attached_media[{i}]"] = '{"media_fbid":"%s"}' % mid
            pid = net.post_json(f"{GRAPH}/{page}/feed", data=data)["id"]
            url = ""
        else:
            data = {"message": text, "access_token": token, **sched}
            if post["link"]:
                data["link"] = post["link"]
            pid = net.post_json(f"{GRAPH}/{page}/feed", data=data)["id"]
            url = ""
        if not url:
            try:
                url = net.get_json(f"{GRAPH}/{pid}", params={"fields": "permalink_url", "access_token": token}).get("permalink_url", "")
            except PublishError:
                url = f"https://www.facebook.com/{pid}"
        if not sched:
            self._comment(pid, token, post["first_comment"])
        return {"remote_id": pid, "url": url}

    def metrics(self, account, creds, remote_id):
        d = net.get_json(f"{GRAPH}/{remote_id}", params={
            "fields": "reactions.summary(true).limit(0),comments.summary(true).limit(0),shares", "access_token": creds["token"]})
        return {"likes": ((d.get("reactions") or {}).get("summary") or {}).get("total_count", 0),
                "comments": ((d.get("comments") or {}).get("summary") or {}).get("total_count", 0),
                "shares": (d.get("shares") or {}).get("count", 0)}


class Instagram(_Meta):
    key, name, color, abbr = "instagram", "Instagram", "#E1306C", "IG"
    text_limit = 2200
    max_images = 10
    needs_media = True
    allows_mixed = True
    supports = ("first_comment",)
    note = "Instagram downloads your photo/video from a temporary secure link — PostPilot opens one automatically while posting."
    inbox = ("comments",)
    inbox_note = "Comments and replies on your posts. Instagram DMs need Meta app review, so they stay in the Instagram app."

    def check(self, account, creds):
        net.get_json(f"{GRAPH}/{account['external_id']}", params={"fields": "id,username", "access_token": creds["token"]})
        return {}

    def precheck(self, account, creds):
        try:
            d = net.get_json(f"{GRAPH}/{account['external_id']}/content_publishing_limit",
                             params={"fields": "quota_usage,config", "access_token": creds["token"]})
        except PublishError as e:
            if e.kind == "auth":
                raise
            return
        row = (d.get("data") or [{}])[0]
        total = (row.get("config") or {}).get("quota_total")
        if total and row.get("quota_usage", 0) >= total:
            raise PublishError(f"Instagram's limit of {total} posts per 24 hours is used up — waiting.", "rate", retry_after=3600)

    def inbox_fetch(self, account, creds, state, posts):
        me = (account.get("extra_d") or {}).get("username", "")
        out = []
        for p in posts:
            d = net.get_json(f"{GRAPH}/{p['remote_id']}/comments", params={
                "fields": "id,text,timestamp,username,replies{id,text,timestamp,username}", "limit": 50, "access_token": creds["token"]})
            for c in d.get("data", []):
                out.append(item(c["id"], "comment", c.get("text", ""), reply_to=c["id"], thread_key=p["remote_id"], post=p,
                                author_handle="@" + c.get("username", ""), author_name=c.get("username", ""), url=p.get("url", ""),
                                created_at=ts_of(c.get("timestamp")), mine=c.get("username") == me))
                for r in (c.get("replies") or {}).get("data", []):
                    out.append(item(r["id"], "reply", r.get("text", ""), reply_to=c["id"], thread_key=p["remote_id"], post=p,
                                    author_handle="@" + r.get("username", ""), author_name=r.get("username", ""), url=p.get("url", ""),
                                    created_at=ts_of(r.get("timestamp")), mine=r.get("username") == me))
        return out, state

    def inbox_reply(self, account, creds, it, text):
        d = net.post_json(f"{GRAPH}/{it['reply_to']}/replies", data={"message": text, "access_token": creds["token"]})
        return {"remote_id": d.get("id", "")}

    def validate(self, post):
        errs = super().validate(post)
        if len(post["text"].split("#")) - 1 > 30:
            errs.append("Instagram allows at most 30 hashtags.")
        return errs

    def _container(self, ig, token, **params):
        return net.post_json(f"{GRAPH}/{ig}/media", data={**params, "access_token": token})["id"]

    def _wait(self, cid, token, timeout=600):
        end = time.time() + timeout
        while time.time() < end:
            st = net.get_json(f"{GRAPH}/{cid}", params={"fields": "status_code,status", "access_token": token})
            code = st.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram could not process the media: {st.get('status') or code}")
            time.sleep(4)
        raise PublishError("Instagram took too long to process the video — will retry.", "transient")

    def publish(self, account, creds, post):
        ig, token = account["external_id"], creds["token"]
        media = post["media"]
        caption = post["text"]
        if len(media) == 1:
            m = media[0]
            if m["kind"] == "video":
                cid = self._container(ig, token, media_type="REELS", video_url=post["public_url"](m), caption=caption, share_to_feed="true")
            else:
                cid = self._container(ig, token, image_url=post["public_url"](m), caption=caption,
                                      **({"alt_text": m["alt"]} if m.get("alt") else {}))
            self._wait(cid, token)
        else:
            children = []
            for m in media[:10]:
                if m["kind"] == "video":
                    c = self._container(ig, token, media_type="VIDEO", video_url=post["public_url"](m), is_carousel_item="true")
                else:
                    c = self._container(ig, token, image_url=post["public_url"](m), is_carousel_item="true",
                                        **({"alt_text": m["alt"]} if m.get("alt") else {}))
                children.append(c)
            for c in children:
                self._wait(c, token)
            cid = self._container(ig, token, media_type="CAROUSEL", children=",".join(children), caption=caption)
            self._wait(cid, token)
        mid = net.post_json(f"{GRAPH}/{ig}/media_publish", data={"creation_id": cid, "access_token": token})["id"]
        try:
            url = net.get_json(f"{GRAPH}/{mid}", params={"fields": "permalink", "access_token": token}).get("permalink", "")
        except PublishError:
            url = ""
        self._comment(mid, token, post["first_comment"])
        return {"remote_id": mid, "url": url}

    def metrics(self, account, creds, remote_id):
        d = net.get_json(f"{GRAPH}/{remote_id}", params={"fields": "like_count,comments_count", "access_token": creds["token"]})
        return {"likes": d.get("like_count", 0), "comments": d.get("comments_count", 0)}
