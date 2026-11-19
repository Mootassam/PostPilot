"""Telegram channels, Discord, Mastodon and Bluesky — networks that connect with a token or app password."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

import net
from connectors.base import TAG_RE, URL_RE, Connector, PublishError, html_text, item, split_thread, ts_of


# ================================================================== Telegram (Bot API)
class Telegram(Connector):
    key, name, color, abbr = "telegram", "Telegram channel", "#229ED9", "tg"
    text_limit = 4096
    max_images = 10
    allows_mixed = True
    connect = "form"
    form_fields = [{"key": "bot_token", "label": "Bot token", "secret": True, "help": "From @BotFather → /newbot"},
                   {"key": "chat", "label": "Channel or group", "help": "@yourchannel — or the numeric chat id (-100…) for private channels"}]
    guide = ["In Telegram open <b>@BotFather</b> → <code>/newbot</code> → copy the bot token.",
             "Add the bot to your channel as an <b>admin</b> with the right to <b>post messages</b>.",
             "Paste the token and your channel's @username below.",
             "Optional — to see <b>comments</b> in the Inbox: in @BotFather send <code>/setprivacy</code> → Disable, and add the bot to your channel's discussion group."]
    inbox = ("comments", "dms")
    inbox_note = "Messages people send to your bot, and comments in your channel's discussion group (bot privacy mode off)."

    def check(self, account, creds):
        bot = self._api(creds, "getMe")
        m = self._api(creds, "getChatMember", data={"chat_id": account["external_id"], "user_id": bot["id"]})
        warns = []
        if m.get("status") not in ("administrator", "creator"):
            raise PublishError(f"@{bot['username']} is no longer an admin of the channel — add it back as admin.", "auth")
        if m.get("can_post_messages") is False:
            warns.append("The bot is admin but can't post messages — enable “Post messages” for it.")
        return {"warnings": warns}

    def inbox_fetch(self, account, creds, state, posts):
        offset = int(state.get("offset") or 0)
        try:
            ups = self._api(creds, "getUpdates", data={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])})
        except PublishError as e:
            if "webhook" in str(e).lower() or "conflict" in str(e).lower():
                raise PublishError("This bot is used by another app (webhook or another program reading updates).", "plan") from None
            raise
        channel = str(account["external_id"])
        out = []
        for u in ups:
            offset = max(offset, u["update_id"] + 1)
            m = u.get("message") or {}
            fr, chat = m.get("from") or {}, m.get("chat") or {}
            if not m or fr.get("is_bot"):
                continue
            name = " ".join(x for x in [fr.get("first_name"), fr.get("last_name")] if x) or fr.get("username") or "Telegram user"
            text = m.get("text") or m.get("caption") or ("📷 Photo" if m.get("photo") else "(message)")
            ref = {"chat": chat.get("id"), "msg": m.get("message_id")}
            if chat.get("type") == "private":
                out.append(item(f"{chat['id']}:{m['message_id']}", "dm", text, reply_to=ref, thread_key=f"dm:{chat['id']}", author_id=fr.get("id"),
                                author_name=name, author_handle="@" + fr["username"] if fr.get("username") else "", created_at=m.get("date", 0)))
            else:
                parent = m.get("reply_to_message") or {}
                origin = parent.get("forward_from_chat") or (parent.get("sender_chat") if parent.get("is_automatic_forward") else None) or {}
                if str(origin.get("id")) == channel or parent.get("is_automatic_forward"):
                    out.append(item(f"{chat['id']}:{m['message_id']}", "comment", text, reply_to=ref, thread_key=f"g:{chat['id']}:{parent.get('message_id')}",
                                    author_id=fr.get("id"), author_name=name, author_handle="@" + fr["username"] if fr.get("username") else "",
                                    created_at=m.get("date", 0)))
        return out, {**state, "offset": offset}

    def inbox_reply(self, account, creds, it, text):
        ref = json.loads(it["reply_to"])
        r = self._api(creds, "sendMessage", data={"chat_id": ref["chat"], "text": text,
                                                  "reply_parameters": json.dumps({"message_id": ref["msg"], "allow_sending_without_reply": True})})
        return {"remote_id": str(r["message_id"])}

    def _api(self, creds, method, **kw):
        r = net.request("POST", f"https://api.telegram.org/bot{creds['bot_token']}/{method}", ok=(200, 400, 401, 403, 429), **kw)
        d = r.json()
        if not d.get("ok"):
            desc = d.get("description", "Telegram error")
            if r.status_code == 429:
                raise PublishError(desc, "rate", 429, (d.get("parameters") or {}).get("retry_after"))
            if r.status_code == 401:
                raise PublishError("The bot token is invalid or was revoked.", "auth", 401)
            raise PublishError(desc + (" — make the bot an admin of the channel." if "not enough rights" in desc or "chat not found" in desc else ""))
        return d["result"]

    def connect_form(self, data):
        creds = {"bot_token": (data.get("bot_token") or "").strip()}
        chat = (data.get("chat") or "").strip()
        chat = re.sub(r"^(https?://)?t\.me/", "@", chat)
        if not creds["bot_token"] or not chat:
            raise PublishError("Bot token and channel are required.")
        bot = self._api(creds, "getMe")
        c = self._api(creds, "getChat", data={"chat_id": chat})
        try:
            me = self._api(creds, "getChatMember", data={"chat_id": c["id"], "user_id": bot["id"]})
            if me.get("status") != "administrator":
                raise PublishError(f"@{bot['username']} is not an admin of this channel yet — add it as admin with “Post messages”.")
        except PublishError as e:
            if "admin" in str(e):
                raise
        return [{"network": "telegram", "name": c.get("title") or chat, "handle": "@" + c["username"] if c.get("username") else "private",
                 "external_id": str(c["id"]), "avatar": "", "creds": creds, "extra": {"username": c.get("username", ""), "bot": bot["username"]}}]

    def publish(self, account, creds, post):
        chat = account["external_id"]
        text = post["text"]
        media = post["media"]
        caption_ok = len(text) <= 1024
        if not media:
            r = self._api(creds, "sendMessage", data={"chat_id": chat, "text": text, "link_preview_options": json.dumps({"is_disabled": False})})
            mid = r["message_id"]
        elif len(media) == 1:
            m = media[0]
            method, field = {"video": ("sendVideo", "video"), "gif": ("sendAnimation", "animation")}.get(m["kind"], ("sendPhoto", "photo"))
            with open(m["path"], "rb") as f:
                r = self._api(creds, method, data={"chat_id": chat, **({"caption": text} if caption_ok else {})},
                              files={field: (m["name"], f, m["mime"])}, timeout=(15, 900))
            mid = r["message_id"]
        else:
            files, group = {}, []
            handles = []
            try:
                for i, m in enumerate(media[:10]):
                    fh = open(m["path"], "rb")
                    handles.append(fh)
                    files[f"f{i}"] = (m["name"], fh, m["mime"])
                    item = {"type": "video" if m["kind"] == "video" else "photo", "media": f"attach://f{i}"}
                    if i == 0 and caption_ok:
                        item["caption"] = text
                    group.append(item)
                r = self._api(creds, "sendMediaGroup", data={"chat_id": chat, "media": json.dumps(group)}, files=files, timeout=(15, 900))
            finally:
                for fh in handles:
                    fh.close()
            mid = r[0]["message_id"]
        if media and not caption_ok:  # caption too long for media → send the text as its own message
            self._api(creds, "sendMessage", data={"chat_id": chat, "text": text})
        uname = account["extra_d"].get("username")
        return {"remote_id": str(mid), "url": f"https://t.me/{uname}/{mid}" if uname else ""}


# ================================================================== Discord (webhook)
class Discord(Connector):
    key, name, color, abbr = "discord", "Discord", "#5865F2", "dc"
    text_limit = 2000
    max_images = 10
    allows_mixed = True
    connect = "form"
    form_fields = [{"key": "webhook", "label": "Webhook URL", "secret": True},
                   {"key": "name", "label": "Name to show in PostPilot", "help": "e.g. My server #announcements"}]
    guide = ["In Discord: <b>Server settings → Integrations → Webhooks → New webhook</b>.", "Pick the channel, then <b>Copy webhook URL</b> and paste it below."]
    inbox_note = "Webhooks can only post — Discord replies stay in Discord."

    def check(self, account, creds):
        net.get_json(creds["webhook"], auth_statuses=(401, 404))
        return {}

    def connect_form(self, data):
        url = (data.get("webhook") or "").strip()
        if not re.match(r"^https://(ptb\.|canary\.)?discord(app)?\.com/api/webhooks/\d+/[\w-]+", url):
            raise PublishError("That is not a Discord webhook URL.")
        d = net.get_json(url)
        return [{"network": "discord", "name": data.get("name") or d.get("name") or "Discord webhook", "handle": f"#{d.get('channel_id', '')}",
                 "external_id": d.get("id", ""), "avatar": "", "creds": {"webhook": url},
                 "extra": {"guild_id": d.get("guild_id", ""), "channel_id": d.get("channel_id", "")}}]

    def publish(self, account, creds, post):
        files, handles = {}, []
        try:
            for i, m in enumerate(post["media"][:10]):
                fh = open(m["path"], "rb")
                handles.append(fh)
                files[f"files[{i}]"] = (m["name"], fh, m["mime"])
            d = net.post_json(creds["webhook"] + "?wait=true", data={"payload_json": json.dumps({"content": post["text"]})},
                              files=files or None, auth_statuses=(401, 404))
        finally:
            for fh in handles:
                fh.close()
        extra = account["extra_d"]
        url = f"https://discord.com/channels/{extra.get('guild_id')}/{d.get('channel_id')}/{d.get('id')}" if extra.get("guild_id") else ""
        return {"remote_id": d.get("id", ""), "url": url}


# ================================================================== Mastodon
class Mastodon(Connector):
    key, name, color, abbr = "mastodon", "Mastodon", "#6364FF", "M"
    text_limit = 500
    max_images = 4
    supports = ("thread",)
    connect = "form"
    form_fields = [{"key": "instance", "label": "Server", "help": "e.g. mastodon.social"},
                   {"key": "token", "label": "Access token", "secret": True, "help": "Preferences → Development → New application (scopes: read write) → Your access token"}]
    guide = ["On your Mastodon server open <b>Preferences → Development → New application</b>.",
             "Name it “PostPilot”, keep scopes <b>read</b> and <b>write</b>, save.",
             "Open the app and copy <b>Your access token</b> — paste it below with your server name."]

    inbox = ("mentions", "dms")
    inbox_note = "Mentions, replies and direct messages."

    def _base(self, creds):
        return "https://" + re.sub(r"^https?://", "", creds["instance"]).strip("/")

    def check(self, account, creds):
        net.get_json(self._base(creds) + "/api/v1/accounts/verify_credentials", headers={"Authorization": f"Bearer {creds['token']}"})
        return {}

    def inbox_fetch(self, account, creds, state, posts):
        q = {"types[]": "mention", "limit": 40}
        if state.get("since"):
            q["since_id"] = state["since"]
        d = net.get_json(self._base(creds) + "/api/v1/notifications", params=q, headers={"Authorization": f"Bearer {creds['token']}"})
        ours = {p["remote_id"]: p for p in posts}
        out = []
        for n in d if isinstance(d, list) else []:
            st, acc = n.get("status") or {}, n.get("account") or {}
            if not st:
                continue
            post = ours.get(st.get("in_reply_to_id"))
            kind = "dm" if st.get("visibility") == "direct" else ("reply" if post else "mention")
            out.append(item(st["id"], kind, html_text(st.get("content", "")),
                            reply_to={"id": st["id"], "acct": acc.get("acct", ""), "visibility": st.get("visibility", "public")},
                            thread_key=st.get("in_reply_to_id") or st["id"], post=post, author_id=acc.get("id", ""),
                            author_name=acc.get("display_name") or acc.get("username", ""), author_handle="@" + acc.get("acct", ""),
                            author_avatar=acc.get("avatar", ""), url=st.get("url", ""), created_at=ts_of(st.get("created_at"))))
        newest = max((n["id"] for n in d), key=lambda x: int(x) if str(x).isdigit() else 0) if isinstance(d, list) and d else None
        return out, ({**state, "since": newest} if newest else state)

    def inbox_reply(self, account, creds, it, text):
        ref = json.loads(it["reply_to"])
        body = text if text.lstrip().startswith("@" + ref["acct"]) else f"@{ref['acct']} {text}"
        d = net.post_json(self._base(creds) + "/api/v1/statuses", headers={"Authorization": f"Bearer {creds['token']}"},
                          json={"status": body, "in_reply_to_id": ref["id"], "visibility": ref.get("visibility") or "public"})
        return {"remote_id": d.get("id", ""), "url": d.get("url", "")}

    def connect_form(self, data):
        creds = {"instance": (data.get("instance") or "").strip(), "token": (data.get("token") or "").strip()}
        if not creds["instance"] or not creds["token"]:
            raise PublishError("Server and access token are required.")
        me = net.get_json(self._base(creds) + "/api/v1/accounts/verify_credentials", headers={"Authorization": f"Bearer {creds['token']}"})
        host = re.sub(r"^https?://", "", creds["instance"]).strip("/")
        return [{"network": "mastodon", "name": me.get("display_name") or me["username"], "handle": f"@{me['username']}@{host}",
                 "external_id": me["id"], "avatar": me.get("avatar", ""), "creds": creds, "extra": {}}]

    def publish(self, account, creds, post):
        base, h = self._base(creds), {"Authorization": f"Bearer {creds['token']}"}
        ids = []
        for m in post["media"]:
            with open(m["path"], "rb") as f:
                d = net.post_json(base + "/api/v2/media", headers=h, data={"description": m.get("alt", "")[:1500]},
                                  files={"file": (m["name"], f, m["mime"])}, timeout=(15, 900))
            for _ in range(60):
                if d.get("url"):
                    break
                time.sleep(3)
                d = net.get_json(f"{base}/api/v1/media/{d['id']}", headers=h, ok=(200, 206))
            ids.append(d["id"])
        parts = split_thread(post["text"], self.text_limit, "mastodon")
        first = prev = None
        url = ""
        for i, p in enumerate(parts):
            body = {"status": p, "visibility": "public"}
            if i == 0 and ids:
                body["media_ids"] = ids
            if prev:
                body["in_reply_to_id"] = prev
            try:
                d = net.post_json(base + "/api/v1/statuses", headers={**h, "Idempotency-Key": f"pp-{post['target_id']}-{i}"}, json=body)
            except PublishError as e:
                if first:  # never repost the live first part
                    return {"remote_id": first, "url": url, "units": i, "warning": f"Only {i} of {len(parts)} thread parts were posted: {e}"}
                raise
            prev = d["id"]
            if not first:
                first, url = d["id"], d.get("url", "")
        return {"remote_id": first, "url": url, "units": len(parts)}

    def metrics(self, account, creds, remote_id):
        d = net.get_json(f"{self._base(creds)}/api/v1/statuses/{remote_id}", headers={"Authorization": f"Bearer {creds['token']}"})
        return {"likes": d.get("favourites_count", 0), "shares": d.get("reblogs_count", 0), "comments": d.get("replies_count", 0)}


# ================================================================== Bluesky (AT Protocol)
def facets(text: str) -> list[dict]:
    """Clickable links and #hashtags (Bluesky needs UTF-8 byte offsets)."""
    out = []
    b = text.encode("utf-8")

    def span(start_char, end_char):
        return {"byteStart": len(text[:start_char].encode("utf-8")), "byteEnd": len(text[:end_char].encode("utf-8"))}

    for m in URL_RE.finditer(text):
        uri = m.group(0).rstrip(".,;:!?")
        out.append({"index": span(m.start(), m.start() + len(uri)), "features": [{"$type": "app.bsky.richtext.facet#link", "uri": uri}]})
    for m in TAG_RE.finditer(text):
        tag = m.group(1)
        if tag.isdigit():
            continue
        out.append({"index": span(m.start(), m.end()), "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}]})
    del b
    return out


class Bluesky(Connector):
    key, name, color, abbr = "bluesky", "Bluesky", "#0085FF", "bs"
    text_limit = 300
    max_images = 4
    allows_video = False
    supports = ("thread",)
    connect = "form"
    form_fields = [{"key": "handle", "label": "Handle", "help": "e.g. yourname.bsky.social"},
                   {"key": "password", "label": "App password", "secret": True, "help": "Settings → Privacy and security → App passwords → Add"}]
    guide = ["In Bluesky open <b>Settings → Privacy and security → App passwords</b> → <b>Add app password</b>.",
             "Paste your handle and that app password below (never your real password)."]
    inbox = ("mentions",)
    inbox_note = "Replies, mentions and quotes of your posts."

    def check(self, account, creds):
        self._session(creds["handle"], creds["password"])
        return {}

    def inbox_fetch(self, account, creds, state, posts):
        sess, pds = self._session(creds["handle"], creds["password"])
        d = net.get_json(f"{pds}/xrpc/app.bsky.notification.listNotifications", params={"limit": 50},
                         headers={"Authorization": f"Bearer {sess['accessJwt']}"})
        ours = {p["remote_id"]: p for p in posts}
        out = []
        for n in d.get("notifications", []):
            if n.get("reason") not in ("reply", "mention", "quote"):
                continue
            rec, au = n.get("record") or {}, n.get("author") or {}
            parent = {"uri": n["uri"], "cid": n["cid"]}
            root = ((rec.get("reply") or {}).get("root")) or parent
            post = ours.get(((rec.get("reply") or {}).get("parent") or {}).get("uri")) or ours.get(root.get("uri"))
            rkey = n["uri"].rsplit("/", 1)[-1]
            out.append(item(n["uri"], "reply" if n["reason"] == "reply" else "mention", rec.get("text", ""),
                            reply_to={"parent": parent, "root": {"uri": root["uri"], "cid": root["cid"]}}, thread_key=root.get("uri", ""),
                            post=post, author_id=au.get("did", ""), author_name=au.get("displayName") or au.get("handle", ""),
                            author_handle="@" + au.get("handle", ""), author_avatar=au.get("avatar", ""),
                            url=f"https://bsky.app/profile/{au.get('handle')}/post/{rkey}", created_at=ts_of(n.get("indexedAt"))))
        return out, state

    def inbox_reply(self, account, creds, it, text):
        ref = json.loads(it["reply_to"])
        sess, pds = self._session(creds["handle"], creds["password"])
        rec = {"$type": "app.bsky.feed.post", "text": text, "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               "reply": {"root": ref["root"], "parent": ref["parent"]}}
        fc = facets(text)
        if fc:
            rec["facets"] = fc
        d = net.post_json(f"{pds}/xrpc/com.atproto.repo.createRecord", headers={"Authorization": f"Bearer {sess['accessJwt']}"},
                          json={"repo": sess["did"], "collection": "app.bsky.feed.post", "record": rec})
        return {"remote_id": d["uri"]}

    def _session(self, handle, password):
        d = net.post_json("https://bsky.social/xrpc/com.atproto.server.createSession", json={"identifier": handle, "password": password},
                          auth_statuses=(401,))
        pds = "https://bsky.social"
        for s in ((d.get("didDoc") or {}).get("service") or []):
            if s.get("id", "").endswith("#atproto_pds"):
                pds = s.get("serviceEndpoint", pds)
        return d, pds

    def connect_form(self, data):
        handle = (data.get("handle") or "").strip().lstrip("@")
        pw = (data.get("password") or "").strip()
        if not handle or not pw:
            raise PublishError("Handle and app password are required.")
        d, pds = self._session(handle, pw)
        prof = {}
        try:
            prof = net.get_json("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile", params={"actor": d["did"]})
        except PublishError:
            pass
        return [{"network": "bluesky", "name": prof.get("displayName") or d["handle"], "handle": "@" + d["handle"], "external_id": d["did"],
                 "avatar": prof.get("avatar", ""), "creds": {"handle": handle, "password": pw}, "extra": {"pds": pds}}]

    def publish(self, account, creds, post):
        sess, pds = self._session(creds["handle"], creds["password"])
        h = {"Authorization": f"Bearer {sess['accessJwt']}"}
        images = []
        for m in post["media"][:4]:
            with open(m["path"], "rb") as f:
                blob = net.post_json(f"{pds}/xrpc/com.atproto.repo.uploadBlob", headers={**h, "Content-Type": "image/jpeg"}, data=f.read())["blob"]
            img = {"alt": m.get("alt", ""), "image": blob}
            if m.get("width") and m.get("height"):
                img["aspectRatio"] = {"width": m["width"], "height": m["height"]}
            images.append(img)
        parts = split_thread(post["text"], self.text_limit, "bluesky")
        root = parent = None
        first_uri = ""
        for i, p in enumerate(parts):
            rec = {"$type": "app.bsky.feed.post", "text": p, "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
            fc = facets(p)
            if fc:
                rec["facets"] = fc
            if i == 0 and images:
                rec["embed"] = {"$type": "app.bsky.embed.images", "images": images}
            if parent:
                rec["reply"] = {"root": root, "parent": parent}
            try:
                d = net.post_json(f"{pds}/xrpc/com.atproto.repo.createRecord", headers=h,
                                  json={"repo": sess["did"], "collection": "app.bsky.feed.post", "record": rec})
            except PublishError as e:
                if first_uri:  # never repost the live first part
                    rk = first_uri.rsplit("/", 1)[-1]
                    return {"remote_id": first_uri, "url": f"https://bsky.app/profile/{sess['handle']}/post/{rk}", "units": i,
                            "warning": f"Only {i} of {len(parts)} thread parts were posted: {e}"}
                raise
            ref = {"uri": d["uri"], "cid": d["cid"]}
            root = root or ref
            parent = ref
            first_uri = first_uri or d["uri"]
        rkey = first_uri.rsplit("/", 1)[-1]
        return {"remote_id": first_uri, "url": f"https://bsky.app/profile/{sess['handle']}/post/{rkey}", "units": len(parts)}

    def metrics(self, account, creds, remote_id):
        d = net.get_json("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts", params={"uris": remote_id})
        p = (d.get("posts") or [{}])[0]
        return {"likes": p.get("likeCount", 0), "shares": p.get("repostCount", 0) + p.get("quoteCount", 0), "comments": p.get("replyCount", 0)}
