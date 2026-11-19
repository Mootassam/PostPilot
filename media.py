"""Media library: save uploads, thumbnails, and per-network preparation (resize / convert / compress / pad).

Every network has different rules (Bluesky images ≤ 976 KB, Instagram JPEG with an aspect ratio between
4:5 and 1.91:1, X ≤ 5 MB…). `prepare()` makes a compliant copy automatically and caches it.
"""

from __future__ import annotations

import glob
import hashlib
import mimetypes
import os
import secrets
import shutil
import subprocess

from PIL import Image, ImageOps

import app_paths
import store

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}

# per-network image rules: max bytes, max long side, force jpeg, allowed aspect range (w/h) or None
IMAGE_RULES = {
    "instagram": (8_000_000, 1440, True, (0.8, 1.91)),
    "threads":   (8_000_000, 1440, True, (0.5, 1.91)),
    "facebook":  (10_000_000, 2048, False, None),
    "x":         (4_900_000, 2048, False, None),
    "bluesky":   (950_000, 2000, True, None),
    "mastodon":  (8_000_000, 2560, False, None),
    "linkedin":  (8_000_000, 2560, False, None),
    "pinterest": (19_000_000, 2560, False, None),
    "telegram":  (9_500_000, 2560, True, None),
    "discord":   (7_500_000, 4096, False, None),
}


def media_dir() -> str:
    d = os.path.join(app_paths.data_dir(), "media")
    os.makedirs(d, exist_ok=True)
    return d


def cache_dir() -> str:
    d = os.path.join(media_dir(), "cache")
    os.makedirs(d, exist_ok=True)
    return d


def kind_of(name: str, mime: str = "") -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext == ".gif":
        return "gif"
    if ext in IMAGE_EXT or mime.startswith("image/"):
        return "image"
    if ext in VIDEO_EXT or mime.startswith("video/"):
        return "video"
    return "file"


_FFMPEG: list = []


def ffmpeg_path() -> str:
    """ffmpeg.exe if one is available (PP_FFMPEG, PATH, or a winget install); '' otherwise."""
    if _FFMPEG:
        return _FFMPEG[0]
    found = os.environ.get("PP_FFMPEG") or shutil.which("ffmpeg") or ""
    if not found:
        base = os.environ.get("LOCALAPPDATA") or ""
        hits = sorted(glob.glob(os.path.join(base, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "**", "ffmpeg.exe"), recursive=True))
        link = os.path.join(base, "Microsoft", "WinGet", "Links", "ffmpeg.exe")
        found = link if os.path.exists(link) else (hits[-1] if hits else "")
    _FFMPEG.append(found)
    return found


def video_thumb(path: str) -> str:
    """Save a JPEG poster frame next to the video (a representative frame a few seconds in). '' when not possible."""
    ff = ffmpeg_path()
    if not ff:
        return ""
    dest = path + ".thumb.jpg"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for seek in ("3", "0"):
        try:
            subprocess.run([ff, "-v", "error", "-y", "-ss", seek, "-i", path, "-vf", "thumbnail=30,scale=480:-2",
                            "-frames:v", "1", "-q:v", "4", dest], capture_output=True, timeout=60, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            return ""
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
    return ""


def ensure_thumb(m: dict) -> dict:
    """Give a video row a poster image if it has none yet (e.g. uploaded before this existed)."""
    if m and m["kind"] == "video" and not (m.get("thumb") and os.path.exists(m["thumb"])) and os.path.exists(m["path"]):
        th = video_thumb(m["path"])
        if th:
            store.execute("UPDATE media SET thumb=? WHERE id=?", (th, m["id"]))
            m = dict(m, thumb=th)
    return m


def backfill_thumbs() -> int:
    n = 0
    for m in store.rows("SELECT * FROM media WHERE kind='video' AND (thumb IS NULL OR thumb='')"):
        n += 1 if ensure_thumb(m).get("thumb") else 0
    return n


def save_file(src_path: str, name: str, brand_id: int, move: bool = False) -> dict:
    """Store a file in the library and return the media row."""
    ext = os.path.splitext(name)[1].lower() or ".bin"
    dest = os.path.join(media_dir(), secrets.token_hex(10) + ext)
    (shutil.move if move else shutil.copyfile)(src_path, dest)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    kind = kind_of(name, mime)
    w = h = None
    thumb = ""
    if kind in ("image", "gif"):
        try:
            with Image.open(dest) as im:
                w, h = im.size
                th = ImageOps.exif_transpose(im).convert("RGB")
                th.thumbnail((480, 480))
                thumb = dest + ".thumb.jpg"
                th.save(thumb, "JPEG", quality=82)
        except Exception as e:  # noqa: BLE001
            os.remove(dest)
            raise ValueError(f"“{name}” is not a valid image ({e}).") from None
    elif kind == "video":
        thumb = video_thumb(dest)
    mid = store.execute("INSERT INTO media(brand_id,name,path,kind,mime,size,width,height,thumb,token,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (brand_id, name[:200], dest, kind, mime, os.path.getsize(dest), w, h, thumb,
                         secrets.token_urlsafe(16), store.now()))
    return store.one("SELECT * FROM media WHERE id=?", (mid,))


def out(m: dict) -> dict:
    d = {k: m[k] for k in ("id", "name", "kind", "mime", "size", "width", "height", "alt", "created_at")}
    d["url"] = f"/media/{m['id']}/file"
    has_thumb = bool(m.get("thumb"))
    # videos without a poster get no thumb URL: the UI then shows a frame from the video itself
    d["thumb"] = f"/media/{m['id']}/thumb" if has_thumb else (d["url"] if m["kind"] in ("image", "gif") else "")
    d["poster"] = f"/media/{m['id']}/thumb" if has_thumb and m["kind"] == "video" else ""
    d["tags"] = store.jl(m.get("tags"))
    return d


def delete(mid: int) -> None:
    m = store.one("SELECT * FROM media WHERE id=?", (mid,))
    if not m:
        return
    for p in (m["path"], m["thumb"]):
        try:
            if p:
                os.remove(p)
        except OSError:
            pass
    store.execute("DELETE FROM media WHERE id=?", (mid,))


def prepare(m: dict, network: str) -> str:
    """Path of a copy of media `m` that satisfies `network`'s rules (original if already fine)."""
    if m["kind"] not in ("image",) or network not in IMAGE_RULES:
        return m["path"]
    max_bytes, max_side, force_jpeg, aspect = IMAGE_RULES[network]
    w, h = m.get("width") or 0, m.get("height") or 0
    ratio = (w / h) if w and h else 1
    ext = os.path.splitext(m["path"])[1].lower()
    fine = (os.path.getsize(m["path"]) <= max_bytes and max(w, h) <= max_side
            and (not force_jpeg or ext in (".jpg", ".jpeg"))
            and (not aspect or aspect[0] <= ratio <= aspect[1]))
    if fine:
        return m["path"]
    key = hashlib.sha1(f"{m['path']}|{network}|{os.path.getmtime(m['path'])}".encode()).hexdigest()[:16]
    dest = os.path.join(cache_dir(), f"{key}.jpg")
    if os.path.exists(dest):
        return dest
    with Image.open(m["path"]) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        if aspect:
            r = im.width / im.height
            if r < aspect[0]:   # too tall → pad left/right
                nw = int(im.height * aspect[0] + 0.5)
                canvas = Image.new("RGB", (nw, im.height), (255, 255, 255))
                canvas.paste(im, ((nw - im.width) // 2, 0))
                im = canvas
            elif r > aspect[1]:  # too wide → pad top/bottom
                nh = int(im.width / aspect[1] + 0.5)
                canvas = Image.new("RGB", (im.width, nh), (255, 255, 255))
                canvas.paste(im, (0, (nh - im.height) // 2))
                im = canvas
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        quality = 90
        while True:
            im.save(dest, "JPEG", quality=quality, optimize=True, progressive=True)
            if os.path.getsize(dest) <= max_bytes or quality <= 40:
                break
            quality -= 8
            if quality <= 60 and max(im.size) > 1200:
                im.thumbnail((int(max(im.size) * 0.8),) * 2, Image.LANCZOS)
    return dest


def dims(path: str) -> tuple[int, int]:
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return (0, 0)
