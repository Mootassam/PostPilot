"""Path helpers that work from source, from an installed copy and in tests."""

from __future__ import annotations

import os
import sys

APP_NAME = "PostPilot"
HERE = os.path.dirname(os.path.abspath(__file__))


def is_installed() -> bool:
    """True when running from an installed copy (installer drops an `.installed` marker)."""
    return getattr(sys, "frozen", False) or os.path.exists(os.path.join(HERE, ".installed"))


def static_dir() -> str:
    return os.path.join(HERE, "static")


def data_dir() -> str:
    """Writable per-user folder for the database, media, session and logs."""
    override = os.environ.get("PP_DATA")
    if override:
        d = override
    elif is_installed():
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, APP_NAME)
    else:
        d = os.path.join(HERE, "data")
    os.makedirs(d, exist_ok=True)
    return d


def media_dir() -> str:
    d = os.path.join(data_dir(), "media")
    os.makedirs(d, exist_ok=True)
    return d
