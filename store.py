"""SQLite storage for PostPilot (one file, WAL mode, a connection per thread)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime

import app_paths
import vault

_local = threading.local()
_lock = threading.RLock()
DB_PATH = os.path.join(app_paths.data_dir(), "postpilot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS brands(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, color TEXT DEFAULT '#6d5dfc', created_at INTEGER
);

CREATE TABLE IF NOT EXISTS apps(               -- developer-app keys per network (encrypted)
  network TEXT PRIMARY KEY, creds TEXT
);

CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  brand_id INTEGER, network TEXT, name TEXT, handle TEXT DEFAULT '', avatar TEXT DEFAULT '',
  external_id TEXT DEFAULT '', creds TEXT DEFAULT '',     -- encrypted JSON
  status TEXT DEFAULT 'ok',                               -- ok | expired | error | paused
  error TEXT DEFAULT '', expires_at INTEGER,
  extra TEXT DEFAULT '{}',                                -- network specific (board id, username…)
  slots TEXT DEFAULT '',                                  -- posting schedule JSON, '' = brand default
  created_at INTEGER, updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS media(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  brand_id INTEGER, name TEXT, path TEXT, kind TEXT, mime TEXT, size INTEGER,
  width INTEGER, height INTEGER, thumb TEXT DEFAULT '', alt TEXT DEFAULT '',
  token TEXT, tags TEXT DEFAULT '[]', created_at INTEGER
);

CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  brand_id INTEGER,
  status TEXT DEFAULT 'draft',          -- draft | scheduled | published | partial | failed
  text TEXT DEFAULT '',
  media TEXT DEFAULT '[]',              -- media ids in order
  first_comment TEXT DEFAULT '',
  link TEXT DEFAULT '', utm INTEGER DEFAULT 0,
  labels TEXT DEFAULT '[]',
  scheduled_at INTEGER, published_at INTEGER,
  recurring TEXT DEFAULT '',            -- {"every_days":7,"times":0,"done":0}
  source TEXT DEFAULT 'composer', notes TEXT DEFAULT '',
  created_at INTEGER, updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_posts_sched ON posts(status, scheduled_at);

CREATE TABLE IF NOT EXISTS targets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER, account_id INTEGER,
  text TEXT DEFAULT '',                 -- '' = use the post text
  options TEXT DEFAULT '{}',            -- title, board, privacy, thread, alt overrides…
  status TEXT DEFAULT 'pending',        -- pending | publishing | handed (uploaded, network publishes later) | published | failed | reminder | skipped
  remote_id TEXT DEFAULT '', url TEXT DEFAULT '', error TEXT DEFAULT '',
  attempts INTEGER DEFAULT 0, next_try_at INTEGER,
  published_at INTEGER, metrics TEXT DEFAULT '{}', metrics_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_targets_post ON targets(post_id);
CREATE INDEX IF NOT EXISTS ix_targets_status ON targets(status);

CREATE TABLE IF NOT EXISTS ideas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER, title TEXT, body TEXT DEFAULT '',
  stage TEXT DEFAULT 'idea', color TEXT DEFAULT '', created_at INTEGER, updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS hashtag_groups(
  id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER, name TEXT, tags TEXT
);
CREATE TABLE IF NOT EXISTS templates(
  id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER, name TEXT, text TEXT
);

CREATE TABLE IF NOT EXISTS feeds(
  id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER, name TEXT, url TEXT,
  accounts TEXT DEFAULT '[]', template TEXT DEFAULT '{title}

{link}',
  mode TEXT DEFAULT 'queue',            -- queue | draft | now
  every_min INTEGER DEFAULT 60, enabled INTEGER DEFAULT 1,
  last_checked INTEGER, seen TEXT DEFAULT '[]', last_error TEXT DEFAULT '', created_at INTEGER
);

CREATE TABLE IF NOT EXISTS inbox(                 -- unified inbox: comments, replies, mentions, DMs
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER, network TEXT, kind TEXT,      -- comment | reply | mention | dm
  remote_id TEXT, reply_to TEXT DEFAULT '',          -- what to answer (network specific)
  thread_key TEXT DEFAULT '', post_target_id INTEGER, post_text TEXT DEFAULT '', post_url TEXT DEFAULT '',
  author_id TEXT DEFAULT '', author_name TEXT DEFAULT '', author_handle TEXT DEFAULT '', author_avatar TEXT DEFAULT '',
  text TEXT DEFAULT '', url TEXT DEFAULT '', created_at INTEGER,
  status TEXT DEFAULT 'new',                         -- new | read | replied | archived | mine
  tag TEXT DEFAULT '',                               -- question | complaint | positive
  reply_text TEXT DEFAULT '', replied_at INTEGER, fetched_at INTEGER,
  UNIQUE(account_id, remote_id)
);
CREATE INDEX IF NOT EXISTS ix_inbox ON inbox(status, created_at);

CREATE TABLE IF NOT EXISTS plans(                 -- AI content calendar drafts
  id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER, title TEXT, brief TEXT, items TEXT, created_at INTEGER
);

CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, level TEXT, kind TEXT, text TEXT
);
CREATE INDEX IF NOT EXISTS ix_act ON activity(ts);
"""

# columns added after 1.0 — applied to existing databases on start
MIGRATIONS = {
    "accounts": [("last_check_at", "INTEGER"), ("check_error", "TEXT DEFAULT ''"), ("inbox_state", "TEXT DEFAULT '{}'"),
                 ("inbox_synced_at", "INTEGER"), ("inbox_error", "TEXT DEFAULT ''"), ("inbox_retry_at", "INTEGER"),
                 ("rl", "TEXT DEFAULT '{}'"), ("expiry_notified", "TEXT DEFAULT ''")],
    "targets": [("units", "INTEGER DEFAULT 1"), ("limit_note", "TEXT DEFAULT ''"),
                # sent_at = when it was actually sent (differs from published_at when the network schedules it itself)
                ("sent_at", "INTEGER"), ("native_skip", "INTEGER DEFAULT 0")],
}

DEFAULTS = {
    "theme": "light",
    "brand_id": 0,
    "default_slots": json.dumps([{"days": "0123456", "time": t} for t in ("09:00", "13:00", "18:00")]),
    "ai_provider": "auto",          # auto | anthropic | ollama | off
    "anthropic_key": "",            # encrypted
    "ollama_model": "llama3.1",
    "media_host": "auto",           # auto (Cloudflare quick tunnel) | custom | off
    "public_base": "",
    "utm_source": "{network}",
    "utm_medium": "social",
    "utm_campaign": "postpilot",
    "retry_max": 4,
    "notify": 1,
    "x_plan": "free",
    "limits": "{}",            # per-network overrides {"x": [[17, 86400]], ...}
    "health_hour": 8,
    "health_last_day": "",
    "inbox_every_min": 10,
    "native_schedule": 1,      # hand scheduled posts to YouTube / Facebook early so they go out even with the PC off
}
INT_KEYS = {"brand_id", "retry_max", "notify", "health_hour", "inbox_every_min", "native_schedule"}


def now() -> int:
    return int(time.time())


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None or getattr(_local, "path", None) != DB_PATH:
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        _local.c, _local.path = c, DB_PATH
    return c


def init(path: str | None = None) -> None:
    global DB_PATH
    if path:
        DB_PATH = path
    with _lock:
        c = conn()
        c.executescript(SCHEMA)
        c.commit()
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        c.commit()
        if not one("SELECT 1 FROM brands LIMIT 1"):
            bid = execute("INSERT INTO brands(name,color,created_at) VALUES(?,?,?)", ("My Brand", "#6d5dfc", now()))
            set("brand_id", bid)
            for name, tags in [("Marketing", "#marketing #digitalmarketing #socialmedia #business #growth"),
                               ("Small business", "#smallbusiness #entrepreneur #startup #shoplocal #supportsmallbusiness")]:
                execute("INSERT INTO hashtag_groups(brand_id,name,tags) VALUES(?,?,?)", (bid, name, tags))
            for name, text in [("Product launch", "🚀 Introducing {product}!\n\n{benefit}\n\n👉 {link}"),
                               ("Tip of the day", "💡 Tip of the day: {tip}\n\nSave this post for later!"),
                               ("Question", "Quick question for you 👇\n\n{question}\n\nTell us in the comments!")]:
                execute("INSERT INTO templates(brand_id,name,text) VALUES(?,?,?)", (bid, name, text))
        # anything that was mid-publish when the app stopped goes back to the queue
        execute("UPDATE targets SET status='pending' WHERE status='publishing'")


def rows(sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def scalar(sql: str, args=(), default=None):
    r = conn().execute(sql, args).fetchone()
    return r[0] if r and r[0] is not None else default


def execute(sql: str, args=()) -> int:
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def update(sql: str, args=()) -> int:
    """Run an UPDATE/DELETE and return how many rows changed (used to claim work atomically)."""
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.rowcount


def executemany(sql: str, seq) -> None:
    with _lock:
        c = conn()
        c.executemany(sql, seq)
        c.commit()


# ---------------------------------------------------------------- settings
def get(key: str):
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    v = r["value"] if r else DEFAULTS.get(key)
    if key in INT_KEYS:
        try:
            return int(float(v or 0))
        except (TypeError, ValueError):
            return int(DEFAULTS.get(key) or 0)
    return v if v is not None else ""


def set(key: str, value) -> None:  # noqa: A001
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, "" if value is None else str(value)))


def get_secret(key: str) -> str:
    return vault.decrypt(get(key) or "")


def set_secret(key: str, value: str) -> None:
    set(key, vault.encrypt(value) if value else "")


def log(kind: str, text: str, level: str = "info") -> None:
    execute("INSERT INTO activity(ts,level,kind,text) VALUES(?,?,?,?)", (now(), level, kind, text[:800]))


# ---------------------------------------------------------------- json + secrets helpers
def jl(v, default=None):
    try:
        x = json.loads(v) if v else default
    except (TypeError, ValueError):
        x = default
    return x if x is not None else ([] if default is None else default)


def jd(v) -> dict:
    x = jl(v, {})
    return x if isinstance(x, dict) else {}


def creds_of(account: dict) -> dict:
    return jd(vault.decrypt(account.get("creds") or ""))


def save_creds(account_id: int, creds: dict) -> None:
    execute("UPDATE accounts SET creds=?, updated_at=? WHERE id=?", (vault.encrypt(json.dumps(creds)), now(), account_id))


def app_creds(network: str) -> dict:
    r = one("SELECT creds FROM apps WHERE network=?", (network,))
    return jd(vault.decrypt(r["creds"])) if r else {}


def set_app_creds(network: str, creds: dict) -> None:
    execute("INSERT INTO apps(network,creds) VALUES(?,?) ON CONFLICT(network) DO UPDATE SET creds=excluded.creds",
            (network, vault.encrypt(json.dumps(creds))))


def local_dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts)
