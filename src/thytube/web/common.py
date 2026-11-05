"""Shared bits for the web layer: dependencies, errors, serialisers, a TTL cache."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, Header, Request

from .. import db
from ..config import Config
from ..pool import url_for

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def get_cfg(request: Request) -> Config:
    return request.app.state.cfg


def get_conn(cfg: Config = Depends(get_cfg)) -> Iterator[sqlite3.Connection]:
    conn = db.connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def profile_id(x_profile: str | None = Header(default=None)) -> int | None:
    if not x_profile:
        return None
    try:
        return int(x_profile)
    except ValueError:
        return None


def check_video_id(video_id: str) -> str:
    if not VIDEO_ID.match(video_id):
        raise ApiError(400, "bad_video_id", "not a YouTube video ID")
    return video_id


# ---- cursors -------------------------------------------------------------------


def encode_cursor(values: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(values).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> list[Any] | None:
    if not cursor:
        return None
    try:
        pad = "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(cursor + pad))
    except (ValueError, json.JSONDecodeError):
        raise ApiError(400, "bad_cursor", "invalid cursor") from None


# ---- serialisers -----------------------------------------------------------------

VIDEO_SELECT = (
    "SELECT v.*, c.title AS channel_title, c.handle AS channel_handle, c.avatar_path AS channel_avatar_path,"
    " c.avatar_url AS channel_avatar_url{extra} FROM videos v LEFT JOIN channels c ON c.id = v.channel_id"
)


def video_select(with_profile: bool) -> str:
    extra = (
        ", w.position_s AS my_position, w.watch_count AS my_watch_count, w.last_watched_at AS my_last_watched"
        if with_profile
        else ""
    )
    sql = VIDEO_SELECT.format(extra=extra)
    if with_profile:
        sql += " LEFT JOIN watch_stats w ON w.video_id = v.id AND w.profile_id = :profile"
    return sql


def channel_brief(row: sqlite3.Row | dict[str, Any], prefix: str = "channel_") -> dict[str, Any] | None:
    keys = row.keys()
    cid = row["channel_id"] if "channel_id" in keys else None
    if not cid:
        return None
    return {
        "id": cid,
        "title": row[prefix + "title"] if prefix + "title" in keys else None,
        "handle": row[prefix + "handle"] if prefix + "handle" in keys else None,
        "avatar": url_for(row[prefix + "avatar_path"])
        if prefix + "avatar_path" in keys and row[prefix + "avatar_path"]
        else (row[prefix + "avatar_url"] if prefix + "avatar_url" in keys else None),
    }


def thumb_for(row: sqlite3.Row) -> str | None:
    return (
        url_for(row["thumb_path"]) or row["thumb_url"] or f"https://i.ytimg.com/vi/{row['id']}/mqdefault.jpg"
    )


def video_json(row: sqlite3.Row, *, detail: bool = False) -> dict[str, Any]:
    keys = row.keys()
    out: dict[str, Any] = {
        "id": row["id"],
        "title": row["title"],
        "channel": channel_brief(row),
        "duration_s": row["duration_s"],
        "published_at": row["published_at"],
        "state": row["state"],
        "origin": row["origin"],
        "subscription_id": row["subscription_id"],
        "pinned": bool(row["pinned"]),
        "is_short": bool(row["is_short"]),
        "thumb": thumb_for(row),
        "media_url": url_for(row["file_path"]) if row["state"] == "cached" else None,
        "size_bytes": row["size_bytes"],
        "est_bytes": row["est_bytes"],
        "height": row["height"],
        "vcodec": row["vcodec"],
        "acodec": row["acodec"],
        "codec_warning": bool(row["vcodec"] and (row["vcodec"] != "h264" or row["acodec"] != "aac")),
        "downloaded_at": row["downloaded_at"],
        "evicted_at": row["evicted_at"],
        "total_watch_count": row["total_watch_count"],
        "last_watched_at": row["last_watched_at"],
        "filter_reason": row["filter_reason"],
        "last_error_class": row["last_error_class"],
        "last_error": (row["last_error"] or "")[:600] or None,
        "attempts": row["attempts"],
        "next_attempt_at": row["next_attempt_at"],
    }
    if "my_position" in keys:
        out["me"] = {
            "position_s": row["my_position"] or 0,
            "watch_count": row["my_watch_count"] or 0,
            "last_watched_at": row["my_last_watched"],
        }
    if detail:
        out["description"] = row["description"]
        out["chapters"] = json.loads(row["chapters_json"]) if row["chapters_json"] else []
        out["live_status"] = row["live_status"]
        out["subs_state"] = row["subs_state"]
    return out


def subscription_json(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    out = {
        k: row[k]
        for k in (
            "id",
            "channel_id",
            "include_regex",
            "exclude_regex",
            "min_duration_s",
            "max_duration_s",
            "backfill_count",
            "max_height",
            "created_at",
            "last_polled_at",
            "next_poll_at",
            "last_poll_status",
            "last_poll_error",
        )
    }
    for k in ("enabled", "case_sensitive", "include_shorts", "include_streams", "backfill_done"):
        out[k] = bool(row[k])
    if "channel_title" in keys:
        out["channel"] = channel_brief(row)
    for k in ("cached_count", "cached_bytes", "queued_count"):
        if k in keys:
            out[k] = row[k] or 0
    return out


# ---- a tiny TTL cache -------------------------------------------------------------


class TTLCache:
    def __init__(self, ttl_s: float = 600, max_items: int = 256) -> None:
        self.ttl, self.max = ttl_s, max_items
        self._d: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        hit = self._d.get(key)
        if hit is None:
            return None
        if hit[0] < time.monotonic():
            self._d.pop(key, None)
            return None
        return hit[1]

    def set(self, key: str, value: Any) -> None:
        if len(self._d) >= self.max:
            for k in sorted(self._d, key=lambda k: self._d[k][0])[: self.max // 4]:
                self._d.pop(k, None)
        self._d[key] = (time.monotonic() + self.ttl, value)
