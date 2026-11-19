"""HTTP helper shared by all connectors: one session, timeouts, and errors mapped to what the engine needs.

PublishError.kind tells the publisher what to do:
  auth      → token expired/revoked: mark the account, don't retry
  rate      → rate limited: retry later (retry_after seconds if the network said so)
  transient → network problem / server error: retry with backoff
  invalid   → the post itself was rejected (too long, bad media…): don't retry
"""

from __future__ import annotations

import json

import requests

TIMEOUT = (15, 120)
UA = "PostPilot/1.0 (+desktop)"


class PublishError(Exception):
    def __init__(self, message: str, kind: str = "invalid", status: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after


SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA


def _message(r) -> str:
    try:
        d = r.json()
    except ValueError:
        return (r.text or r.reason or "").strip()[:300]
    if isinstance(d, dict):
        e = d.get("error")
        if isinstance(e, dict):
            msg = e.get("error_user_msg") or e.get("message") or e.get("error_description") or json.dumps(e)[:300]
            return str(msg)
        if isinstance(e, str):
            return d.get("error_description") or d.get("message") or e
        if d.get("errors") and isinstance(d["errors"], list):
            first = d["errors"][0]
            return str(first.get("message") or first.get("detail") or first) if isinstance(first, dict) else str(first)
        for k in ("detail", "message", "description", "title"):
            if d.get(k):
                return str(d[k])
    return str(d)[:300]


def request(method: str, url: str, *, ok=(200, 201, 202, 204), auth_statuses=(401,), **kw):
    """Send a request; return the response or raise PublishError with a readable message."""
    kw.setdefault("timeout", TIMEOUT)
    try:
        r = SESSION.request(method, url, **kw)
    except requests.Timeout:
        raise PublishError("The network did not answer in time.", "transient") from None
    except requests.ConnectionError:
        raise PublishError("Can't reach the network — check your internet connection.", "transient") from None
    except requests.RequestException as e:
        raise PublishError(f"Connection problem: {e}", "transient") from None
    if r.status_code in ok:
        return r
    msg = _message(r) or f"HTTP {r.status_code}"
    if r.status_code in auth_statuses:
        raise PublishError(f"Login expired or access was removed — reconnect this account. ({msg})", "auth", r.status_code)
    if r.status_code == 429:
        ra = r.headers.get("Retry-After") or r.headers.get("x-rate-limit-reset")
        try:
            ra = int(float(ra)) if ra and float(ra) < 10**9 else None
        except ValueError:
            ra = None
        raise PublishError(f"Rate limited by the network — will retry. ({msg})", "rate", 429, ra)
    if r.status_code >= 500:
        raise PublishError(f"The network had a server error ({r.status_code}) — will retry. {msg}", "transient", r.status_code)
    raise PublishError(msg, "invalid", r.status_code)


def get_json(url: str, **kw) -> dict:
    return request("GET", url, **kw).json()


def post_json(url: str, **kw) -> dict:
    r = request("POST", url, **kw)
    if r.status_code == 204 or not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {}
