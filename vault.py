"""Encrypts the Telegram login session at rest.

The session string is encrypted with Fernet (AES). On Windows the Fernet key itself is protected
with DPAPI, so a copied database/key file is useless on another PC or Windows user account.
"""

from __future__ import annotations

import base64
import ctypes
import os
import sys

from cryptography.fernet import Fernet, InvalidToken

import app_paths

KEY_FILE = "secret.key"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0x01, ctypes.byref(out)):  # UI_FORBIDDEN
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _key() -> bytes:
    path = os.path.join(app_paths.data_dir(), KEY_FILE)
    win = sys.platform == "win32"
    if os.path.exists(path):
        raw = open(path, "rb").read()
        if raw.startswith(b"DPAPI:"):
            return _dpapi(base64.b64decode(raw[6:]), protect=False)
        return raw.strip()
    key = Fernet.generate_key()
    data = b"DPAPI:" + base64.b64encode(_dpapi(key, protect=True)) if win else key
    with open(path, "wb") as f:
        f.write(data)
    return key


def encrypt(text: str) -> str:
    return Fernet(_key()).encrypt(text.encode()).decode() if text else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return Fernet(_key()).decrypt(token.encode()).decode()
    except (InvalidToken, OSError, ValueError):
        return ""
