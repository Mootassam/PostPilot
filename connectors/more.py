"""Pinterest, YouTube, Reminder mode (any app without a public posting API) and the demo network."""

from __future__ import annotations

import base64
import os
import random
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import net
from connectors.base import Connector, PublishError, item, ts_of


# ================================================================== Pinterest (API v5)
class Pinterest(Connector):
    key, name, color, abbr = "pinterest", "Pinterest", "#E60023", "P"
    text_limit = 500
    max_images = 1
    allows_video = False
    needs_media = True
    supports = ("title", "board", "link_field")
    connect = "both"
    app_fields = [{"key": "app_id", "label": "App ID"}, {"key": "app_secret", "label": "App secret key", "secret": True}]
    form_fields = [{"key": "token", "label": "Access token (alternative to Log in)", "secret": True,
                    "help": "developers.pinterest.com → My apps → your app → Generate access token"}]
    guide = ["Create an app at <a href='https://developers.pinterest.com/apps/' target='_blank'>developers.pinterest.com/apps</a> (a Business account is free).",
             "Add this redirect URI to the app: <code>{redirect}</code>",
             "Copy the <b>App ID</b> and <b>App secret key</b> below and click <b>Log in with Pinterest</b>.",
             "New apps start with <b>Trial access</b> — enough to post on your own boards."]
    API = "https://api.pinterest.com/v5"
    inbox_note = "Pinterest has no comments API — check comments in the Pinterest app."

    def check(self, account, creds):
        net.get_json(f"{self.API}/user_account", headers=self._h(creds["token"]))
        return {}

    def _h(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _accounts(self, token, refresh=None, expires_in=None):
        me = net.get_json(f"{self.API}/user_account", headers=self._h(token))
        boards = []
        bookmark = None
        for _ in range(10):
            d = net.get_json(f"{self.API}/boards", headers=self._h(token), params={"page_size": 100, **({"bookmark": bookmark} if bookmark else {})})
            boards += [{"id": b["id"], "name": b["name"]} for b in d.get("items", [])]
            bookmark = d.get("bookmark")
            if not bookmark:
                break
        if not boards:
            raise PublishError("Your Pinterest account has no boards yet — create one first.")
        return [{"network": "pinterest", "name": me.get("business_name") or me.get("username"), "handle": "@" + me.get("username", ""),
                 "external_id": me.get("username", ""), "avatar": me.get("profile_image", ""),
                 "creds": {"token": token, **({"refresh": refresh} if refresh else {})},
                 "expires_at": int(time.time()) + int(expires_in) if expires_in else None,
                 "extra": {"boards": boards, "board_id": boards[0]["id"]}}]

    def oauth_url(self, app, redirect, state):
        if not app.get("app_id"):
            raise PublishError("Enter your Pinterest App ID and secret first.")
        return "https://www.pinterest.com/oauth/?" + urlencode({"client_id": app["app_id"], "redirect_uri": redirect, "response_type": "code",
                                                               "scope": "boards:read,pins:read,pins:write,user_accounts:read", "state": state})

    def _token(self, app, data):
        auth = base64.b64encode(f"{app['app_id']}:{app['app_secret']}".encode()).decode()
        return net.post_json(f"{self.API}/oauth/token", headers={"Authorization": f"Basic {auth}"}, data=data)

    def oauth_finish(self, app, redirect, code):
        d = self._token(app, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect})
        return self._accounts(d["access_token"], d.get("refresh_token"), d.get("expires_in"))

    def connect_form(self, data):
        tok = (data.get("token") or "").strip()
        if not tok:
            raise PublishError("Paste an access token or use “Log in with Pinterest”.")
        return self._accounts(tok)

    def refresh(self, account, creds, app):
        if not creds.get("refresh") or not app.get("app_id"):
            return None
        d = self._token(app, {"grant_type": "refresh_token", "refresh_token": creds["refresh"]})
        return {"token": d["access_token"], "refresh": d.get("refresh_token", creds["refresh"]),
                "expires_at": int(time.time()) + int(d.get("expires_in", 2_592_000))}

    def publish(self, account, creds, post):
        extra = account["extra_d"]
        board = post["options"].get("board_id") or extra.get("board_id")
        if not board:
            raise PublishError("Choose a Pinterest board for this pin.")
        m = post["media"][0]
        with open(m["path"], "rb") as f:
            data = base64.b64encode(f.read()).decode()
        body = {"board_id": board, "title": (post["options"].get("title") or "")[:100], "description": post["text_no_link"][:500],
                "media_source": {"source_type": "image_base64", "content_type": "image/png" if m["path"].lower().endswith(".png") else "image/jpeg", "data": data}}
        if post["link"]:
            body["link"] = post["link"]
        if m.get("alt"):
            body["alt_text"] = m["alt"][:500]
        d = net.post_json(f"{self.API}/pins", headers=self._h(creds["token"]), json=body)
        return {"remote_id": d["id"], "url": f"https://www.pinterest.com/pin/{d['id']}/"}

    def metrics(self, account, creds, remote_id):
        d = net.get_json(f"{self.API}/pins/{remote_id}", headers=self._h(creds["token"]), params={"pin_metrics": "true"})
        lt = ((d.get("pin_metrics") or {}).get("lifetime_metrics") or {})
        return {"views": lt.get("impression"), "likes": lt.get("reaction", 0), "shares": lt.get("save", 0), "clicks": lt.get("outbound_click", 0)}


# ================================================================== YouTube (Data API v3)
class YouTube(Connector):
    key, name, color, abbr = "youtube", "YouTube", "#FF0000", "YT"
    text_limit = 5000
    max_images = 0
    needs_media = True
    needs_video = True
    supports = ("title", "privacy", "link_field")
    connect = "oauth"
    app_fields = [{"key": "client_id", "label": "OAuth Client ID"}, {"key": "client_secret", "label": "OAuth Client secret", "secret": True}]
    guide = ["Open <a href='https://console.cloud.google.com/apis/library/youtube.googleapis.com' target='_blank'>Google Cloud Console</a> → create a project → <b>Enable</b> the YouTube Data API v3.",
             "<b>OAuth consent screen</b>: External, add yourself as a <b>Test user</b>.",
             "<b>Credentials → Create credentials → OAuth client ID</b> → type <b>Web application</b> → Authorized redirect URI: <code>{redirect}</code>",
             "Copy the Client ID and secret below and click <b>Log in with Google</b>."]
    note = ("Google keeps videos uploaded by unverified apps private until the app passes Google's audit — you can then make them public in "
            "YouTube Studio. Tip: set the OAuth consent screen to “In production”, otherwise Google ends the login every 7 days.")
    inbox = ("comments",)
    inbox_note = "Comments on your channel's videos."

    def check(self, account, creds):
        tok = self._fresh(account, creds)
        net.get_json("https://www.googleapis.com/youtube/v3/channels", params={"part": "id", "mine": "true"}, headers={"Authorization": f"Bearer {tok}"})
        warns = []
        try:
            info = net.get_json("https://oauth2.googleapis.com/tokeninfo", params={"access_token": tok})
            if "youtube.force-ssl" not in info.get("scope", ""):
                warns.append("To reply to YouTube comments from PostPilot, click Reconnect once (new permission).")
        except PublishError:
            pass
        return {"warnings": warns}

    def inbox_fetch(self, account, creds, state, posts):
        tok = self._fresh(account, creds)
        d = net.get_json("https://www.googleapis.com/youtube/v3/commentThreads", headers={"Authorization": f"Bearer {tok}"}, params={
            "part": "snippet,replies", "allThreadsRelatedToChannelId": account["external_id"], "maxResults": 50, "order": "time", "textFormat": "plainText"})
        ours = {p["remote_id"]: p for p in posts}
        out = []
        for th in d.get("items", []):
            top = th["snippet"]["topLevelComment"]
            vid = th["snippet"].get("videoId", "")
            for c, kind in [(top, "comment")] + [(r, "reply") for r in (th.get("replies") or {}).get("comments", [])]:
                sn = c["snippet"]
                ch = (sn.get("authorChannelId") or {}).get("value", "")
                out.append(item(c["id"], kind, sn.get("textOriginal") or sn.get("textDisplay", ""), reply_to=top["id"], thread_key=vid,
                                post=ours.get(vid), author_id=ch, author_name=sn.get("authorDisplayName", ""),
                                author_avatar=sn.get("authorProfileImageUrl", ""), url=f"https://www.youtube.com/watch?v={vid}&lc={c['id']}",
                                created_at=ts_of(sn.get("publishedAt")), mine=ch == account["external_id"]))
        return out, state

    def inbox_reply(self, account, creds, it, text):
        tok = self._fresh(account, creds)
        try:
            d = net.post_json("https://www.googleapis.com/youtube/v3/comments?part=snippet", headers={"Authorization": f"Bearer {tok}"},
                              json={"snippet": {"parentId": it["reply_to"], "textOriginal": text}})
        except PublishError as e:
            if e.status == 403:
                raise PublishError("YouTube needs one more permission to reply — go to Accounts and click Reconnect on this channel.") from None
            raise
        return {"remote_id": d.get("id", "")}

    def oauth_url(self, app, redirect, state):
        if not app.get("client_id"):
            raise PublishError("Enter your Google OAuth Client ID and secret first.")
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": app["client_id"], "redirect_uri": redirect, "response_type": "code", "state": state, "access_type": "offline",
            "prompt": "consent", "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly "
                                         "https://www.googleapis.com/auth/youtube.force-ssl"})

    def oauth_finish(self, app, redirect, code):
        d = net.post_json("https://oauth2.googleapis.com/token", data={"code": code, "client_id": app["client_id"], "client_secret": app["client_secret"],
                                                                       "redirect_uri": redirect, "grant_type": "authorization_code"})
        tok = d["access_token"]
        ch = net.get_json("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                          headers={"Authorization": f"Bearer {tok}"}).get("items") or []
        if not ch:
            raise PublishError("This Google account has no YouTube channel.")
        sn = ch[0]["snippet"]
        return [{"network": "youtube", "name": sn["title"], "handle": sn.get("customUrl", ""), "external_id": ch[0]["id"],
                 "avatar": ((sn.get("thumbnails") or {}).get("default") or {}).get("url", ""),
                 "creds": {"token": tok, "refresh": d.get("refresh_token", ""), "token_exp": int(time.time()) + int(d.get("expires_in", 3600))},
                 "extra": {}}]

    def _fresh(self, account, creds):
        if creds.get("token_exp", 0) > time.time() + 120:
            return creds["token"]
        from store import app_creds, save_creds
        app = app_creds("youtube")
        d = net.post_json("https://oauth2.googleapis.com/token", data={"client_id": app.get("client_id"), "client_secret": app.get("client_secret"),
                                                                       "refresh_token": creds.get("refresh"), "grant_type": "refresh_token"},
                          auth_statuses=(400, 401))  # invalid_grant = login revoked/expired
        creds.update(token=d["access_token"], token_exp=int(time.time()) + int(d.get("expires_in", 3600)))
        save_creds(account["id"], creds)
        return creds["token"]

    native_schedule = (15 * 60, 180 * 86400)

    def can_native(self, post):
        # YouTube's publishAt turns a private video public at that time, so only for posts meant to be public
        return (post["options"].get("privacy") or "public") == "public"

    def publish(self, account, creds, post):
        tok = self._fresh(account, creds)
        v = next(m for m in post["media"] if m["kind"] == "video")
        title = (post["options"].get("title") or post["text_no_link"].split("\n")[0] or v["name"])[:100]
        status = {"privacyStatus": post["options"].get("privacy") or "public", "selfDeclaredMadeForKids": False}
        if post.get("publish_at"):
            status.update(privacyStatus="private",
                          publishAt=datetime.fromtimestamp(int(post["publish_at"]), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        meta = {"snippet": {"title": title, "description": post["text"][:5000], "categoryId": "22",
                            "tags": [t.lstrip("#") for t in post["text"].split() if t.startswith("#")][:30]},
                "status": status}
        size = os.path.getsize(v["path"])
        r = net.request("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                        headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Type": v["mime"], "X-Upload-Content-Length": str(size)},
                        json=meta)
        loc = r.headers["Location"]
        with open(v["path"], "rb") as f:
            d = net.request("PUT", loc, data=f, headers={"Content-Type": v["mime"], "Content-Length": str(size)}, timeout=(15, 3600)).json()
        return {"remote_id": d["id"], "url": f"https://youtu.be/{d['id']}"}

    def metrics(self, account, creds, remote_id):
        tok = self._fresh(account, creds)
        it = net.get_json("https://www.googleapis.com/youtube/v3/videos", params={"part": "statistics", "id": remote_id},
                          headers={"Authorization": f"Bearer {tok}"}).get("items") or [{}]
        s = it[0].get("statistics", {})
        return {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)), "comments": int(s.get("commentCount", 0))}


# ================================================================== Reminder mode
class Reminder(Connector):
    key, name, color, abbr = "reminder", "Reminder (TikTok, WhatsApp…)", "#F59E0B", "⏰"
    text_limit = 10000
    max_images = 35
    allows_mixed = True
    connect = "form"
    form_fields = [{"key": "name", "label": "Name", "help": "e.g. TikTok @mybrand, WhatsApp Status, Snapchat"},
                   {"key": "app", "label": "App", "type": "select", "options": ["TikTok", "WhatsApp", "Snapchat", "Google Business", "Reddit", "Other"]}]
    guide = ["For apps that don't offer a public posting API, PostPilot <b>reminds you at the scheduled time</b>.",
             "You get a notification with the caption ready to <b>copy</b> and the media ready to <b>download</b> — post it from your phone in seconds, then click <b>Mark as posted</b>."]

    def connect_form(self, data):
        name = (data.get("name") or "").strip() or (data.get("app") or "Reminder")
        return [{"network": "reminder", "name": name, "handle": data.get("app") or "Other", "external_id": "", "avatar": "", "creds": {},
                 "extra": {"app": data.get("app") or "Other"}}]

    def publish(self, account, creds, post):
        return {"remote_id": "", "url": "", "reminder": True}


# ================================================================== Demo network (PP_FAKE=1)
def store_now() -> int:
    return int(time.time())


class Demo(Connector):
    key, name, color, abbr = "demo", "Demo network", "#8B5CF6", "D"
    text_limit = 1000
    max_images = 10
    allows_mixed = True
    supports = ("first_comment",)
    connect = "form"
    form_fields = [{"key": "name", "label": "Account name"}]
    guide = ["A simulated network to try PostPilot without real accounts."]
    fail_next: list[str] = []
    check_fail = ""
    inbox = ("comments", "dms")
    inbox_note = "Simulated comments for the demo."
    replies: list[tuple] = []

    def check(self, account, creds):
        if Demo.check_fail:
            raise PublishError(Demo.check_fail, "auth")
        return {}

    def inbox_fetch(self, account, creds, state, posts):
        r = random.Random(account["id"])
        people = [("Sara B.", "@sara_b"), ("Youssef", "@yous"), ("Emma L.", "@emma"), ("Karim", "@karim_k")]
        lines = ["How much is it? 😍", "Is this available in Casablanca?", "Love this!! 🔥", "Ordered last week — still not delivered, not happy.",
                 "Do you deliver on weekends?", "Amazing quality, thanks!"]
        out = []
        for p in posts:
            for k in range(r.randint(1, 2)):
                who = r.choice(people)
                out.append(item(f"c{p['remote_id']}-{k}", "comment", r.choice(lines), reply_to=f"c{p['remote_id']}-{k}", thread_key=p["remote_id"],
                                post=p, author_name=who[0], author_handle=who[1], created_at=store_now() - r.randint(60, 7200)))
        out.append(item(f"dm-{account['id']}", "dm", "Hi! Can I get your price list please?", reply_to="dm", thread_key="dm",
                        author_name="Lina", author_handle="@lina", created_at=store_now() - 300))
        return out, state

    def inbox_reply(self, account, creds, it, text):
        Demo.replies.append((it["remote_id"], text))
        return {"remote_id": "r" + str(random.randint(1, 10**6))}

    def connect_form(self, data):
        n = (data.get("name") or "Demo account").strip()
        return [{"network": "demo", "name": n, "handle": "@" + n.lower().replace(" ", "_"), "external_id": str(random.randint(1000, 9999)),
                 "avatar": "", "creds": {"token": "demo"}, "extra": {}}]

    def publish(self, account, creds, post):
        if Demo.fail_next:
            kind = Demo.fail_next.pop(0)
            raise PublishError(f"Simulated {kind} error", kind)
        if "FAIL" in post["text"]:
            raise PublishError("The demo network rejected this post (contains FAIL).")
        rid = str(random.randint(10**8, 10**9))
        return {"remote_id": rid, "url": f"https://example.com/demo/{rid}"}

    def metrics(self, account, creds, remote_id):
        r = random.Random(remote_id)
        return {"likes": r.randint(3, 220), "comments": r.randint(0, 30), "shares": r.randint(0, 25), "views": r.randint(200, 9000)}
