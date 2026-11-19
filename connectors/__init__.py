"""Registry of every supported network."""

from __future__ import annotations

import os

from connectors.linkedin import LinkedIn
from connectors.meta import Facebook, Instagram
from connectors.more import Demo, Pinterest, Reminder, YouTube
from connectors.simple import Bluesky, Discord, Mastodon, Telegram
from connectors.threads import Threads
from connectors.x import X

ORDER = [Facebook(), Instagram(), X(), LinkedIn(), Threads(), YouTube(), Pinterest(), Telegram(), Bluesky(), Mastodon(),
         Discord(), Reminder()]
if os.environ.get("PP_FAKE") == "1":
    ORDER.insert(0, Demo())

REGISTRY = {c.key: c for c in ORDER}
# networks that share one developer app / login
APP_KEY = {"facebook": "meta", "instagram": "meta"}


def get(key: str):
    c = REGISTRY.get(key)
    if not c:
        raise KeyError(f"Unknown network {key}")
    return c


def app_key(network: str) -> str:
    return APP_KEY.get(network, network)
