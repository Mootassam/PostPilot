import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["PP_DATA"] = tempfile.mkdtemp(prefix="postpilot-test-")
os.environ["PP_FAKE"] = "1"

import pytest  # noqa: E402
import requests  # noqa: E402
from requests.structures import CaseInsensitiveDict  # noqa: E402

import net  # noqa: E402


def make_response(status=200, body=None, headers=None):
    r = requests.Response()
    r.status_code = status
    if isinstance(body, bytes):
        r._content = body
    elif isinstance(body, str):
        r._content = body.encode()
    else:
        r._content = json.dumps(body).encode()
    r.headers = CaseInsensitiveDict(headers or {})
    r.reason = "OK" if status < 400 else "ERR"
    return r


class FakeSession:
    """Records every request; answers from (method, url-substring) rules (last match wins order: first added first)."""

    def __init__(self):
        self.calls = []
        self.rules = []
        self.headers = {}

    def on(self, method, contains, status=200, body=None, headers=None, times=None):
        self.rules.append({"m": method, "c": contains, "s": status, "b": body if body is not None else {}, "h": headers, "n": times})
        return self

    def request(self, method, url, **kw):
        files = kw.get("files") or {}
        self.calls.append({"method": method, "url": url, "params": kw.get("params"), "data": kw.get("data"), "json": kw.get("json"),
                           "headers": kw.get("headers") or {}, "files": {k: (v[0], v[2] if len(v) > 2 else None) for k, v in files.items()}})
        for rule in self.rules:
            if rule["m"] == method and rule["c"] in url and rule["n"] != 0:
                if rule["n"]:
                    rule["n"] -= 1
                b = rule["b"](kw) if callable(rule["b"]) else rule["b"]
                return make_response(rule["s"], b, rule["h"])
        raise AssertionError(f"Unexpected request: {method} {url}")

    def find(self, contains, method=None):
        return [c for c in self.calls if contains in c["url"] and (not method or c["method"] == method)]


@pytest.fixture
def fake():
    s = FakeSession()
    old = net.SESSION
    net.SESSION = s
    yield s
    net.SESSION = old
