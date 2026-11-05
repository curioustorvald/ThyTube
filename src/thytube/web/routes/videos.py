"""Library, video detail, manual cache, pin, delete; channels."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends

from ... import db
from ...config import Config
from ...pool import Pool, url_for
from ..common import (
    CHANNEL_ID,
    ApiError,
    channel_brief,
    check_video_id,
    decode_cursor,
    encode_cursor,
    get_cfg,
    get_conn,
    profile_id,
    video_json,
    video_select,
)

router = APIRouter()

SORTS = {
    # name: (expression, direction)
    "published": ("COALESCE(v.published_at, v.discovered_at)", "DESC"),
    "downloaded": ("COALESCE(v.downloaded_at, '')", "DESC"),
    "title": ("LOWER(COALESCE(v.title, ''))", "ASC"),
    "duration": ("COALESCE(v.duration_s, 0)", "DESC"),
    "watched": ("COALESCE(v.last_watched_at, '')", "DESC"),
}
STATE_GROUPS = {
    "cached": ["cached"],
    "library": ["cached", "evicted"],
    "queue": ["queued", "downloading", "deferred_space"],
    "problems": ["failed", "unavailable"],
}


@router.get("/videos")
def list_videos(
    channel: str | None = None,
    state: str = "cached",
    sort: str = "published",
    q: str | None = None,
    cursor: str | None = None,
    limit: int = 40,
    conn: sqlite3.Connection = Depends(get_conn),
    profile: int | None = Depends(profile_id),
) -> dict[str, Any]:
    if sort not in SORTS:
        raise ApiError(400, "bad_sort", f"sort must be one of {', '.join(SORTS)}")
    limit = max(1, min(limit, 200))
    states = STATE_GROUPS.get(state) or [s for s in state.split(",") if s]
    expr, direction = SORTS[sort]
    params: dict[str, Any] = {"profile": profile, "limit": limit + 1}
    where = [f"v.state IN ({','.join(f':s{i}' for i in range(len(states)))})"]
    params.update({f"s{i}": s for i, s in enumerate(states)})
    if channel:
        where.append("v.channel_id = :channel")
        params["channel"] = channel
    if q:
        where.append("(v.title LIKE :q ESCAPE '\\' OR c.title LIKE :q ESCAPE '\\')")
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    cur = decode_cursor(cursor)
    if cur:
        op = "<" if direction == "DESC" else ">"
        where.append(f"({expr} {op} :ck OR ({expr} = :ck AND v.id {op} :cid))")
        params["ck"], params["cid"] = cur
    sql = (
        video_select(True)
        + " WHERE "
        + " AND ".join(where)
        + f" ORDER BY {expr} {direction}, v.id {direction} LIMIT :limit"
    )
    rows = conn.execute(sql.replace("SELECT v.*", f"SELECT {expr} AS sort_key, v.*", 1), params).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor([rows[-1]["sort_key"], rows[-1]["id"]]) if more and rows else None
    return {"items": [video_json(r) for r in rows], "next_cursor": next_cursor}


@router.get("/videos/continue")
def continue_watching(
    conn: sqlite3.Connection = Depends(get_conn), profile: int | None = Depends(profile_id)
) -> dict[str, Any]:
    if profile is None:
        return {"items": []}
    rows = conn.execute(
        video_select(True)
        + " WHERE v.state='cached' AND w.position_s > 5 ORDER BY w.last_watched_at DESC LIMIT 12",
        {"profile": profile},
    ).fetchall()
    return {"items": [video_json(r) for r in rows]}


def _get_video(conn: sqlite3.Connection, video_id: str, profile: int | None) -> sqlite3.Row:
    row = conn.execute(
        video_select(True) + " WHERE v.id = :id", {"id": video_id, "profile": profile}
    ).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "video not known")
    return row


@router.get("/videos/{video_id}")
def get_video(
    video_id: str, conn: sqlite3.Connection = Depends(get_conn), profile: int | None = Depends(profile_id)
) -> dict[str, Any]:
    check_video_id(video_id)
    row = _get_video(conn, video_id, profile)
    out = video_json(row, detail=True)
    subs = conn.execute(
        "SELECT lang, kind, path FROM subtitles WHERE video_id=? ORDER BY kind='auto', lang", (video_id,)
    ).fetchall()
    out["subtitles"] = [{"lang": s["lang"], "kind": s["kind"], "url": url_for(s["path"])} for s in subs]
    profiles = conn.execute(
        "SELECT p.id, p.name, p.colour, w.watch_count, w.position_s, w.last_watched_at FROM watch_stats w"
        " JOIN profiles p ON p.id = w.profile_id WHERE w.video_id=? ORDER BY p.name",
        (video_id,),
    ).fetchall()
    out["profiles"] = [dict(p) for p in profiles]
    return out


CACHEABLE_FROM = ("evicted", "failed", "unavailable", "deferred_space", "filtered", "discovered")


@router.post("/videos/{video_id}/cache", status_code=202)
def cache_video(
    video_id: str,
    body: dict[str, Any] | None = Body(default=None),
    conn: sqlite3.Connection = Depends(get_conn),
    profile: int | None = Depends(profile_id),
) -> dict[str, Any]:
    """Manual cache (also "cache again"). The worker picks it up; manual items go first."""
    check_video_id(video_id)
    body = body or {}
    now = db.now_iso()
    max_height = body.get("max_height")
    if max_height is not None and (not isinstance(max_height, int) or max_height < 144):
        raise ApiError(422, "bad_max_height", "max_height must be an integer ≥ 144")
    with db.tx(conn):
        row = conn.execute("SELECT state FROM videos WHERE id=?", (video_id,)).fetchone()
        ch = body.get("channel") or {}
        if ch.get("id") and CHANNEL_ID.match(ch["id"]):
            conn.execute(
                "INSERT INTO channels(id, title, handle, updated_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (ch["id"], ch.get("title"), ch.get("handle"), now),
            )
        if row is None:
            conn.execute(
                "INSERT INTO videos(id, channel_id, title, duration_s, thumb_url, state, origin, max_height,"
                " discovered_at, queued_at) VALUES(?,?,?,?,?,'queued','manual',?,?,?)",
                (
                    video_id,
                    ch.get("id") if ch.get("id") and CHANNEL_ID.match(ch["id"]) else None,
                    body.get("title"),
                    body.get("duration_s"),
                    body.get("thumb"),
                    max_height,
                    now,
                    now,
                ),
            )
        elif row["state"] in CACHEABLE_FROM:
            conn.execute(
                "UPDATE videos SET state='queued', origin='manual', attempts=0, systemic_failures=0,"
                " next_attempt_at=NULL, last_error=NULL, last_error_class=NULL, filter_reason=NULL,"
                " queued_at=?, max_height=COALESCE(?, max_height) WHERE id=?",
                (now, max_height, video_id),
            )
        elif row["state"] == "queued":
            # Bump a waiting item to the front.
            conn.execute("UPDATE videos SET origin='manual', next_attempt_at=NULL WHERE id=?", (video_id,))
    return video_json(_get_video(conn, video_id, profile))


@router.post("/videos/{video_id}/pin")
def pin(
    video_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: Config = Depends(get_cfg),
    profile: int | None = Depends(profile_id),
) -> dict[str, Any]:
    check_video_id(video_id)
    _get_video(conn, video_id, profile)
    pool = Pool(cfg)
    if not pool.can_pin(conn, video_id):
        limit = int(cfg.pool.max_bytes * cfg.pool.max_pinned_ratio)
        raise ApiError(
            409, "pin_limit", f"Pinned videos may use at most {limit / 1e9:.0f} GB; unpin something first."
        )
    conn.execute("UPDATE videos SET pinned=1 WHERE id=?", (video_id,))
    return video_json(_get_video(conn, video_id, profile))


@router.delete("/videos/{video_id}/pin")
def unpin(
    video_id: str, conn: sqlite3.Connection = Depends(get_conn), profile: int | None = Depends(profile_id)
) -> dict[str, Any]:
    check_video_id(video_id)
    conn.execute("UPDATE videos SET pinned=0 WHERE id=?", (video_id,))
    return video_json(_get_video(conn, video_id, profile))


@router.delete("/videos/{video_id}/files", status_code=202)
def delete_files(video_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    check_video_id(video_id)
    row = conn.execute("SELECT state FROM videos WHERE id=?", (video_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "video not known")
    cmd = db.enqueue_command(conn, "delete_files", {"video_ids": [video_id], "reason": "deleted by user"})
    return {"ok": True, "command_id": cmd}


# ---- channels ------------------------------------------------------------------------


CHANNEL_STATS = (
    "SELECT c.*, c.title AS channel_title, c.handle AS channel_handle, c.avatar_path AS channel_avatar_path,"
    " c.avatar_url AS channel_avatar_url, c.id AS channel_id,"
    " (SELECT COUNT(*) FROM videos v WHERE v.channel_id=c.id AND v.state='cached') AS cached_count,"
    " (SELECT COALESCE(SUM(size_bytes),0) FROM videos v WHERE v.channel_id=c.id AND v.state='cached') AS cached_bytes,"
    " (SELECT MAX(downloaded_at) FROM videos v WHERE v.channel_id=c.id AND v.state='cached') AS latest_download,"
    " (SELECT COUNT(*) FROM subscriptions s WHERE s.channel_id=c.id) AS subscription_count"
    " FROM channels c"
)


def channel_json(row: sqlite3.Row) -> dict[str, Any]:
    brief = channel_brief(row) or {}
    return {
        **brief,
        "cached_count": row["cached_count"],
        "cached_bytes": row["cached_bytes"],
        "latest_download": row["latest_download"],
        "subscribed": row["subscription_count"] > 0,
    }


@router.get("/channels")
def list_channels(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    rows = conn.execute(
        CHANNEL_STATS + " WHERE cached_count > 0 OR subscription_count > 0 ORDER BY LOWER(c.title)"
    ).fetchall()
    return {"items": [channel_json(r) for r in rows]}


@router.get("/channels/{channel_id}")
def get_channel(channel_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    row = conn.execute(CHANNEL_STATS + " WHERE c.id=?", (channel_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "channel not known")
    from ..common import subscription_json

    subs = conn.execute(
        "SELECT * FROM subscriptions WHERE channel_id=? ORDER BY id", (channel_id,)
    ).fetchall()
    return {**channel_json(row), "subscriptions": [subscription_json(s) for s in subs]}
