"""What every network connector implements, plus shared text helpers."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from net import PublishError  # noqa: F401 - re-exported for connectors


class Connector:
    key = ""
    name = ""
    color = "#888"
    abbr = "?"
    text_limit = 5000
    max_images = 4
    allows_video = True
    allows_mixed = False         # images + video in the same post
    needs_media = False          # Instagram, Pinterest, YouTube…
    needs_video = False
    supports = ()                # first_comment, thread, title, board, privacy, link_field
    connect = "form"             # form | oauth | both
    app_fields: list[dict] = []  # developer-app keys (OAuth networks)
    form_fields: list[dict] = []
    scopes = ""
    guide: list[str] = []
    note = ""                    # shown in the connect dialog (limitations etc.)
    inbox = ()                   # what the unified inbox reads: comments, mentions, dms
    inbox_note = ""              # inbox limitations of this network (shown in the Inbox)

    def meta(self) -> dict:
        return {"key": self.key, "name": self.name, "color": self.color, "abbr": self.abbr,
                "text_limit": self.text_limit, "max_images": self.max_images, "allows_video": self.allows_video,
                "allows_mixed": self.allows_mixed, "needs_media": self.needs_media, "needs_video": self.needs_video,
                "supports": list(self.supports), "connect": self.connect, "app_fields": self.app_fields,
                "form_fields": self.form_fields, "guide": self.guide, "note": self.note,
                "inbox": list(self.inbox), "inbox_note": self.inbox_note}

    # --- connecting
    def connect_form(self, data: dict) -> list[dict]:
        raise PublishError("This network connects with “Log in”, not a form.")

    def oauth_url(self, app: dict, redirect: str, state: str) -> str:
        raise PublishError("This network does not use OAuth.")

    def oauth_finish(self, app: dict, redirect: str, code: str) -> list[dict]:
        raise PublishError("This network does not use OAuth.")

    def refresh(self, account: dict, creds: dict, app: dict) -> dict | None:
        """Return updated creds (+ optional 'expires_at') when a token can be renewed, else None."""
        return None

    # --- network-side scheduling: upload early, the network publishes at the time by itself (the PC can be off)
    native_schedule: tuple[int, int] | None = None   # (min, max) seconds ahead the network accepts; None = not supported

    def can_native(self, post: dict) -> bool:
        """Whether this particular post can be handed to the network's own scheduler."""
        return self.native_schedule is not None

    # --- using
    def publish(self, account: dict, creds: dict, post: dict) -> dict:
        """post["publish_at"] (unix time) is set when the network should publish it later by itself."""
        raise NotImplementedError

    def metrics(self, account: dict, creds: dict, remote_id: str) -> dict:
        return {}

    # --- health, guard, inbox
    def check(self, account: dict, creds: dict) -> dict:
        """Cheap login test. Raise PublishError(kind='auth') when the login is dead; return {"warnings": [...]}."""
        return {}

    def precheck(self, account: dict, creds: dict) -> None:
        """Last check right before publishing (e.g. the network's live quota). Raise PublishError('rate') to wait."""

    def inbox_fetch(self, account: dict, creds: dict, state: dict, posts: list[dict]) -> tuple[list[dict], dict]:
        """New comments / mentions / DMs → (items, new_state). `posts` = our recent published posts on this account."""
        return [], state

    def inbox_reply(self, account: dict, creds: dict, item: dict, text: str) -> dict:
        raise PublishError(f"{self.name} doesn't allow replying from other apps.")

    def validate(self, post: dict) -> list[str]:
        """Problems that block publishing (shown in the composer)."""
        errs = []
        imgs = [m for m in post["media"] if m["kind"] in ("image", "gif")]
        vids = [m for m in post["media"] if m["kind"] == "video"]
        if self.needs_media and not post["media"]:
            errs.append(f"{self.name} needs a photo or video.")
        if self.needs_video and not vids:
            errs.append(f"{self.name} needs a video.")
        if vids and not self.allows_video:
            errs.append(f"{self.name} doesn't accept videos here.")
        if len(imgs) > self.max_images:
            errs.append(f"{self.name} allows at most {self.max_images} image(s).")
        if len(vids) > 1:
            errs.append(f"{self.name} allows one video per post.")
        if imgs and vids and not self.allows_mixed:
            errs.append(f"{self.name} can't mix photos and a video in one post.")
        if "thread" not in self.supports and length(post["text"], self.key) > self.text_limit:
            errs.append(f"Text is too long for {self.name} ({length(post['text'], self.key)}/{self.text_limit}).")
        return errs


# ------------------------------------------------------------------ text helpers
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
TAG_RE = re.compile(r"(?<![\w&])#([\wÀ-￿][\wÀ-￿_]{0,99})", re.U)


def graphemes(text: str) -> int:
    """Approximate user-perceived characters (combining marks / ZWJ / variation selectors don't count)."""
    n = 0
    skip_next = False
    for ch in text:
        if skip_next:
            skip_next = False
            continue
        if ch == "‍":
            skip_next = True
            continue
        if unicodedata.combining(ch) or ch in ("️", "︎") or 0x1F3FB <= ord(ch) <= 0x1F3FF:
            continue
        n += 1
    return n


def length(text: str, network: str) -> int:
    if network == "x":   # every link counts as 23 characters; most emoji count as 2
        t = URL_RE.sub("x" * 23, text)
        return sum(2 if ord(c) > 0x2FFF else 1 for c in t)
    if network in ("bluesky", "threads"):
        return graphemes(text)
    return len(text)


def split_thread(text: str, limit: int, network: str) -> list[str]:
    """Split long text into a thread at paragraph/sentence/word boundaries, numbering parts (1/3…)."""
    if length(text, network) <= limit:
        return [text]
    budget = limit - 6  # room for " 1/12"
    parts: list[str] = []
    units = re.split(r"(\n\n+)", text)
    chunks: list[str] = []
    for u in units:
        if length(u, network) <= budget:
            chunks.append(u)
        else:
            for s in re.split(r"(?<=[.!?…])\s+", u):
                if length(s, network) <= budget:
                    chunks.append(s + " ")
                else:
                    chunks.extend(w + " " for w in s.split(" "))
    cur = ""
    for c in chunks:
        if length(cur + c, network) <= budget:
            cur += c
        else:
            if cur.strip():
                parts.append(cur.strip())
            cur = c.lstrip()
    if cur.strip():
        parts.append(cur.strip())
    n = len(parts)
    return [f"{p} {i + 1}/{n}" for i, p in enumerate(parts)] if n > 1 else parts


def add_utm(url: str, network: str, source: str, medium: str, campaign: str) -> str:
    if not url:
        return url
    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    fill = {"utm_source": source.replace("{network}", network), "utm_medium": medium.replace("{network}", network),
            "utm_campaign": campaign.replace("{network}", network)}
    for k, v in fill.items():
        if v and k not in q:
            q[k] = v
    return urlunparse(p._replace(query=urlencode(q)))


def ts_of(value) -> int:
    """ISO-8601 / Graph API timestamps → unix seconds."""
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    v = str(value).replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", v):
        v = v[:-2] + ":" + v[-2:]
    try:
        return int(datetime.fromisoformat(v).timestamp())
    except ValueError:
        return 0


def html_text(html: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>\s*<p>", "\n", html or "", flags=re.I)
    return unescape(re.sub(r"<[^>]+>", "", t)).strip()


def item(remote_id, kind, text, *, reply_to="", thread_key="", post=None, author_id="", author_name="", author_handle="",
         author_avatar="", url="", created_at=0, mine=False) -> dict:
    """One unified-inbox entry."""
    return {"remote_id": str(remote_id), "kind": kind, "text": text or "", "reply_to": reply_to if isinstance(reply_to, str) else json.dumps(reply_to),
            "thread_key": str(thread_key or ""), "post_target_id": (post or {}).get("target_id"), "post_text": ((post or {}).get("text") or "")[:300],
            "post_url": (post or {}).get("url") or "", "author_id": str(author_id or ""), "author_name": author_name or author_handle or "Someone",
            "author_handle": author_handle or "", "author_avatar": author_avatar or "", "url": url or "", "created_at": created_at or 0, "mine": bool(mine)}


def hashtags(text: str) -> list[str]:
    return TAG_RE.findall(text)
