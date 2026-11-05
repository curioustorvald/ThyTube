"""Short-lived yt-dlp calls made by the web process (search, resolve, filter preview).

Every call has a hard timeout, results are cached for 10 minutes, and outcomes are reported into
the shared health counters.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import db
from ..config import Config
from ..discovery import channel_meta_from_listing, flat_entries
from ..health import Health
from ..ytdlp import classify as C
from ..ytdlp.runner import Runner, channel_url
from .common import CHANNEL_ID, VIDEO_ID, ApiError, TTLCache

_cache = TTLCache(600)
_sems: dict[int, asyncio.Semaphore] = {}


def _sem() -> asyncio.Semaphore:
    """At most two concurrent yt-dlp calls from the web process (per event loop)."""
    loop = id(asyncio.get_running_loop())
    if loop not in _sems:
        _sems.clear()
        _sems[loop] = asyncio.Semaphore(2)
    return _sems[loop]


class YtError(ApiError):
    def __init__(self, k: C.Classification) -> None:
        status = 404 if k.cls == C.VIDEO else 502
        super().__init__(status, f"youtube_{k.cls}", k.message or k.reason, reason=k.reason)


def _report(cfg: Config, k: C.Classification, source: str) -> None:
    conn = db.connect(cfg.db_path)
    try:
        Health(cfg).record(conn, k, source)
    finally:
        conn.close()


async def _run_json(
    cfg: Config, argv: list[str], source: str, timeout: float | None = None
) -> dict[str, Any]:
    async with _sem():
        res = await Runner(cfg).run_async(argv, timeout or cfg.ytdlp.search_timeout_s)
    k = C.classify(res)
    if k.cls == C.VIDEO:
        raise YtError(k)
    await asyncio.to_thread(_report, cfg, k, source)
    if k.cls != C.OK:
        raise YtError(k)
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        raise ApiError(502, "youtube_bad_json", "yt-dlp returned invalid JSON") from None


async def search(cfg: Config, q: str, kind: str, n: int = 20) -> list[dict[str, Any]]:
    key = f"search:{kind}:{n}:{q}"
    if (hit := _cache.get(key)) is not None:
        return hit
    runner = Runner(cfg)
    data = await _run_json(cfg, runner.search_argv(q, n, kind), "search")
    out: list[dict[str, Any]] = []
    for e in data.get("entries") or []:
        if not e:
            continue
        thumbs = e.get("thumbnails") or []
        if kind == "channel":
            cid = e.get("channel_id") or e.get("id")
            if not cid or not CHANNEL_ID.match(cid):
                continue
            avatar = thumbs[-1]["url"] if thumbs else None
            if avatar and avatar.startswith("//"):
                avatar = "https:" + avatar
            out.append(
                {
                    "type": "channel",
                    "id": cid,
                    "title": e.get("channel") or e.get("title"),
                    "handle": e.get("uploader_id")
                    if str(e.get("uploader_id") or "").startswith("@")
                    else None,
                    "avatar": avatar,
                    "followers": e.get("channel_follower_count"),
                    "description": (e.get("description") or "")[:300],
                }
            )
        else:
            vid = e.get("id")
            if not vid or not VIDEO_ID.match(vid):
                continue
            out.append(
                {
                    "type": "video",
                    "id": vid,
                    "title": e.get("title"),
                    "duration_s": int(e["duration"]) if e.get("duration") else None,
                    "channel": {"id": e.get("channel_id"), "title": e.get("channel") or e.get("uploader")},
                    "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                    "views": e.get("view_count"),
                    "live_status": e.get("live_status"),
                }
            )
    _cache.set(key, out)
    return out


def parse_url(text: str) -> tuple[str, str] | None:
    """('video', id) | ('channel', UC…) | ('channel_url', url) | None."""
    t = text.strip()
    if VIDEO_ID.match(t) and not t.startswith("UC"):
        return ("video", t)
    if CHANNEL_ID.match(t):
        return ("channel", t)
    if re.match(r"^@[\w.\-]{2,}$", t):
        return ("channel_url", f"https://www.youtube.com/{t}")
    if not re.match(r"^https?://", t):
        if re.match(r"^(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", t):
            t = "https://" + t
        else:
            return None
    u = urlparse(t)
    host = (u.hostname or "").lower()
    if host.endswith("youtu.be"):
        vid = u.path.strip("/").split("/")[0]
        return ("video", vid) if VIDEO_ID.match(vid) else None
    if not (host.endswith("youtube.com") or host.endswith("youtube-nocookie.com")):
        return None
    parts = [p for p in u.path.split("/") if p]
    qs = parse_qs(u.query)
    if parts[:1] == ["watch"] and qs.get("v") and VIDEO_ID.match(qs["v"][0]):
        return ("video", qs["v"][0])
    if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v") and VIDEO_ID.match(parts[1]):
        return ("video", parts[1])
    if len(parts) >= 2 and parts[0] == "channel" and CHANNEL_ID.match(parts[1]):
        return ("channel", parts[1])
    if parts and (parts[0].startswith("@") or parts[0] in ("c", "user")):
        base = "/".join(parts[:1] if parts[0].startswith("@") else parts[:2])
        return ("channel_url", f"https://www.youtube.com/{base}")
    return None


async def channel_listing(
    cfg: Config, channel_id: str, tab: str = "videos", limit: int = 50
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = f"listing:{channel_id}:{tab}:{limit}"
    if (hit := _cache.get(key)) is not None:
        return hit
    runner = Runner(cfg)
    data = await _run_json(
        cfg,
        runner.flat_listing_argv(channel_url(channel_id, tab), limit, interactive=True),
        "preview",
        timeout=max(cfg.ytdlp.search_timeout_s, 90),
    )
    entries = flat_entries(data)
    if tab == "shorts":
        for e in entries:
            e["is_short"] = True
    meta = channel_meta_from_listing(data)
    meta["id"] = channel_id
    result = (entries, meta)
    _cache.set(key, result)
    return result


async def resolve_channel_url(cfg: Config, url: str) -> dict[str, Any]:
    key = f"chanurl:{url}"
    if (hit := _cache.get(key)) is not None:
        return hit
    runner = Runner(cfg)
    data = await _run_json(
        cfg, runner.flat_listing_argv(url.rstrip("/") + "/videos", 1, interactive=True), "resolve"
    )
    meta = channel_meta_from_listing(data)
    if not meta.get("id") or not CHANNEL_ID.match(meta["id"]):
        raise ApiError(404, "not_a_channel", "could not find a channel at that URL")
    _cache.set(key, meta)
    return meta


async def video_metadata(cfg: Config, video_id: str) -> dict[str, Any]:
    key = f"video:{video_id}"
    if (hit := _cache.get(key)) is not None:
        return hit
    runner = Runner(cfg)
    info = await _run_json(
        cfg,
        runner.metadata_argv(video_id, interactive=True),
        "resolve",
        timeout=max(cfg.ytdlp.search_timeout_s, 60),
    )
    brief = {
        "id": video_id,
        "title": info.get("title"),
        "duration_s": int(info["duration"]) if info.get("duration") else None,
        "channel": {
            "id": info.get("channel_id"),
            "title": info.get("channel") or info.get("uploader"),
            "handle": info.get("uploader_id") if str(info.get("uploader_id") or "").startswith("@") else None,
        },
        "thumb": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        "live_status": info.get("live_status"),
        "description": (info.get("description") or "")[:1000],
    }
    _cache.set(key, brief)
    return brief
