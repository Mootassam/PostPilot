"""LinkedIn personal profile — official Posts API (versioned REST). Tokens last 60 days."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

import net
from connectors.base import Connector, PublishError

REST = "https://api.linkedin.com/rest"


def api_version() -> str:
    """LinkedIn wants a YYYYMM version and retires each one after ~1 year: use the month before last."""
    d = datetime.now().replace(day=1) - timedelta(days=40)
    return d.strftime("%Y%m")


def escape(text: str) -> str:
    """LinkedIn 'little text' format: these characters must be escaped or the post is cut/rejected."""
    t = re.sub(r"([\\|{}@\[\]()<>#*_~])", r"\\\1", text)
    # turn #words back into real, clickable LinkedIn hashtags
    return re.sub(r"\\#([\w\u00C0-\uFFFF]+)", lambda m: "{hashtag|\\#|" + m.group(1).replace("\\_", "_") + "}", t)


class LinkedIn(Connector):
    key, name, color, abbr = "linkedin", "LinkedIn", "#0A66C2", "in"
    text_limit = 3000
    max_images = 20
    supports = ("link_field",)
    connect = "both"
    scopes = "openid profile w_member_social"
    app_fields = [{"key": "client_id", "label": "Client ID"}, {"key": "client_secret", "label": "Client secret", "secret": True}]
    form_fields = [{"key": "token", "label": "Access token (alternative to Log in)", "secret": True,
                    "help": "LinkedIn Developer portal → Docs and tools → OAuth Token Tools → Create token (scopes: openid profile w_member_social)"}]
    guide = [
        "Go to <a href='https://www.linkedin.com/developers/apps' target='_blank'>linkedin.com/developers/apps</a> → <b>Create app</b> (a LinkedIn Page is required — your own is fine).",
        "In <b>Products</b> request <b>Share on LinkedIn</b> and <b>Sign In with LinkedIn using OpenID Connect</b> (both are instant).",
        "In <b>Auth → Authorized redirect URLs</b> add: <code>{redirect}</code>",
        "Copy the <b>Client ID</b> and <b>Client secret</b> below and click <b>Log in with LinkedIn</b>.",
    ]
    note = "LinkedIn tokens last 60 days — PostPilot reminds you to reconnect a week before."
    inbox_note = "LinkedIn only lets approved partners read comments, so LinkedIn comments stay on LinkedIn."

    def check(self, account, creds):
        net.get_json("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {creds['token']}"})
        return {}

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}", "LinkedIn-Version": api_version(), "X-Restli-Protocol-Version": "2.0.0"}

    def _profile(self, token, expires_in=None):
        me = net.get_json("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {token}"})
        return [{"network": "linkedin", "name": me.get("name") or "LinkedIn member", "handle": me.get("email", ""),
                 "external_id": me["sub"], "avatar": me.get("picture", ""), "creds": {"token": token},
                 "expires_at": int(time.time()) + int(expires_in) if expires_in else int(time.time()) + 60 * 86400,
                 "extra": {}}]

    def oauth_url(self, app, redirect, state):
        if not app.get("client_id"):
            raise PublishError("Enter your LinkedIn Client ID and secret first.")
        return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(
            {"response_type": "code", "client_id": app["client_id"], "redirect_uri": redirect, "state": state, "scope": self.scopes})

    def oauth_finish(self, app, redirect, code):
        d = net.post_json("https://www.linkedin.com/oauth/v2/accessToken", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
            "client_id": app["client_id"], "client_secret": app["client_secret"]})
        return self._profile(d["access_token"], d.get("expires_in"))

    def connect_form(self, data):
        tok = (data.get("token") or "").strip()
        if not tok:
            raise PublishError("Paste an access token or use “Log in with LinkedIn”.")
        return self._profile(tok)

    def _upload_image(self, token, author, m):
        d = net.post_json(f"{REST}/images?action=initializeUpload", headers=self._headers(token),
                          json={"initializeUploadRequest": {"owner": author}})["value"]
        with open(m["path"], "rb") as f:
            net.request("PUT", d["uploadUrl"], data=f.read(), headers={"Authorization": f"Bearer {token}"}, ok=(200, 201))
        return d["image"]

    def _upload_video(self, token, author, m):
        size = os.path.getsize(m["path"])
        d = net.post_json(f"{REST}/videos?action=initializeUpload", headers=self._headers(token), json={
            "initializeUploadRequest": {"owner": author, "fileSizeBytes": size, "uploadCaptions": False, "uploadThumbnail": False}})["value"]
        etags = []
        with open(m["path"], "rb") as f:
            for part in d["uploadInstructions"]:
                f.seek(part["firstByte"])
                chunk = f.read(part["lastByte"] - part["firstByte"] + 1)
                r = net.request("PUT", part["uploadUrl"], data=chunk, headers={"Content-Type": "application/octet-stream"}, ok=(200, 201))
                etags.append(r.headers.get("etag", "").strip('"'))
        net.request("POST", f"{REST}/videos?action=finalizeUpload", headers=self._headers(token), json={
            "finalizeUploadRequest": {"video": d["video"], "uploadToken": d.get("uploadToken", ""), "uploadedPartIds": etags}})
        for _ in range(90):  # wait until LinkedIn processed it
            st = net.get_json(f"{REST}/videos/{quote(d['video'])}", headers=self._headers(token))
            if st.get("status") == "AVAILABLE":
                break
            if st.get("status") == "PROCESSING_FAILED":
                raise PublishError("LinkedIn could not process the video.")
            time.sleep(4)
        return d["video"]

    def publish(self, account, creds, post):
        token = creds["token"]
        author = f"urn:li:person:{account['external_id']}"
        body = {"author": author, "commentary": escape(post["text_no_link"] if (post["link"] and not post["media"]) else post["text"]),
                "visibility": "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
        imgs = [m for m in post["media"] if m["kind"] in ("image", "gif")]
        vids = [m for m in post["media"] if m["kind"] == "video"]
        if vids:
            body["content"] = {"media": {"id": self._upload_video(token, author, vids[0]), "title": (post["options"].get("title") or "Video")[:200]}}
        elif len(imgs) == 1:
            body["content"] = {"media": {"id": self._upload_image(token, author, imgs[0]), "altText": imgs[0].get("alt", "")[:4086]}}
        elif imgs:
            body["content"] = {"multiImage": {"images": [{"id": self._upload_image(token, author, m), "altText": m.get("alt", "")[:4086]}
                                                         for m in imgs[:20]]}}
        elif post["link"]:
            body["content"] = {"article": {"source": post["link"], "title": (post["options"].get("title") or post["link"])[:400]}}
        r = net.request("POST", f"{REST}/posts", headers=self._headers(token), json=body, ok=(201,))
        urn = r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id") or ""
        return {"remote_id": urn, "url": f"https://www.linkedin.com/feed/update/{urn}/" if urn else ""}
