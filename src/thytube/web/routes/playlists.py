"""Playlists: made by hand, or imported (and re-synced) from a YouTube playlist.

Items are rows in `videos`. A video that isn't cached yet is stored as state 'discovered' with origin
'playlist', which nothing acts on until it is cached (here, from the Watch page, or by a subscription).
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends

from ... import db
from ...config import Config
from .. import yt
from ..common import (
    CHANNEL_ID,
    VIDEO_ID,
    ApiError,
    check_video_id,
    get_cfg,
    get_conn,
    profile_id,
    thumb_for,
    video_json,
    video_select,
)
from .videos import CACHEABLE_FROM

router = APIRouter()

MAX_ITEMS = 5000
PREVIEW_ITEMS = 50

PLAYLIST_SELECT = (
    "SELECT p.*, pr.name AS owner_name, pr.colour AS owner_colour,"
    " (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = p.id) AS item_count,"
    " (SELECT COUNT(*) FROM playlist_items i JOIN videos v ON v.id = i.video_id"
    "   WHERE i.playlist_id = p.id AND v.state = 'cached') AS cached_count,"
    " (SELECT COUNT(*) FROM playlist_items i JOIN videos v ON v.id = i.video_id"
    "   WHERE i.playlist_id = p.id AND v.state IN ('queued', 'downloading')) AS queued_count,"
    " (SELECT COALESCE(SUM(v.duration_s), 0) FROM playlist_items i JOIN videos v ON v.id = i.video_id"
    "   WHERE i.playlist_id = p.id) AS duration_s"
    " FROM playlists p LEFT JOIN profiles pr ON pr.id = p.profile_id"
)


def playlist_json(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    thumbs = conn.execute(
        "SELECT v.id, v.thumb_path, v.thumb_url FROM playlist_items i JOIN videos v ON v.id = i.video_id"
        " WHERE i.playlist_id = ? ORDER BY i.position LIMIT 4",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "owner": {"id": row["profile_id"], "name": row["owner_name"], "colour": row["owner_colour"]}
        if row["profile_id"] is not None
        else None,
        "source": {
            "id": row["source_id"],
            "title": row["source_title"],
            "owner": row["source_owner"],
            "url": f"https://www.youtube.com/playlist?list={row['source_id']}",
        }
        if row["source_id"]
        else None,
        "auto_cache": bool(row["auto_cache"]),
        "count": row["item_count"],
        "cached_count": row["cached_count"],
        "queued_count": row["queued_count"],
        "duration_s": row["duration_s"],
        "thumbs": [thumb_for(t) for t in thumbs],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "synced_at": row["synced_at"],
    }


def _get(conn: sqlite3.Connection, pid: int) -> sqlite3.Row:
    row = conn.execute(PLAYLIST_SELECT + " WHERE p.id = ?", (pid,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "no such playlist")
    return row


def _title(value: Any, field: str = "title") -> str:
    title = str(value or "").strip()
    if not title or len(title) > 120:
        raise ApiError(422, "bad_title", "a title of 1–120 characters is required", field=field)
    return title


def _description(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) > 2000:
        raise ApiError(422, "bad_description", "the description may be at most 2000 characters")
    return text or None


def _touch(conn: sqlite3.Connection, pid: int) -> None:
    conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (db.now_iso(), pid))


def _next_position(conn: sqlite3.Connection, pid: int) -> int:
    return conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_items WHERE playlist_id = ?", (pid,)
    ).fetchone()[0]


def _ensure_video(conn: sqlite3.Connection, e: dict[str, Any], *, queue: bool) -> str | None:
    """Make sure `videos` knows this entry; with `queue`, also queue it for caching if it can be.
    Returns the state the video was left in. `e` is a flat entry or a search/watch-page brief."""
    now = db.now_iso()
    ch = e.get("channel")
    ch_id = e.get("channel_id") or (ch.get("id") if isinstance(ch, dict) else None)
    ch_title = ch.get("title") if isinstance(ch, dict) else ch
    if not (ch_id and CHANNEL_ID.match(ch_id)):
        ch_id = None
    if ch_id:
        conn.execute(
            "INSERT INTO channels(id, title, updated_at) VALUES(?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET title = COALESCE(channels.title, excluded.title)",
            (ch_id, ch_title, now),
        )
    row = conn.execute("SELECT state FROM videos WHERE id = ?", (e["id"],)).fetchone()
    duration = e.get("duration_s", e.get("duration"))
    if row is None:
        state = "queued" if queue else "discovered"
        conn.execute(
            "INSERT INTO videos(id, channel_id, title, duration_s, thumb_url, is_short, live_status, state,"
            " origin, discovered_at, queued_at) VALUES(?,?,?,?,?,?,?,?,'playlist',?,?)",
            (
                e["id"],
                ch_id,
                e.get("title"),
                int(duration) if duration else None,
                e.get("thumb_url") or e.get("thumb"),
                1 if e.get("is_short") else 0,
                e.get("live_status"),
                state,
                now,
                now if queue else None,
            ),
        )
        return state
    if queue and row["state"] in CACHEABLE_FROM:
        _requeue(conn, [e["id"]])
        return "queued"
    return row["state"]


def _requeue(conn: sqlite3.Connection, ids: list[str], max_height: int | None = None) -> None:
    """Queue videos for caching at the playlist's (normal) priority. Origin 'playlist' means no
    subscription rules are re-applied at download time."""
    now = db.now_iso()
    conn.executemany(
        "UPDATE videos SET state='queued', origin='playlist', attempts=0, systemic_failures=0,"
        " next_attempt_at=NULL, last_error=NULL, last_error_class=NULL, filter_reason=NULL,"
        " queued_at=?, max_height=COALESCE(?, max_height) WHERE id=?",
        [(now, max_height, i) for i in ids],
    )


def _prune_orphans(conn: sqlite3.Connection) -> None:
    """Forget videos we only knew from a playlist they are no longer in."""
    conn.execute(
        "DELETE FROM videos WHERE state = 'discovered' AND origin = 'playlist'"
        " AND id NOT IN (SELECT video_id FROM playlist_items)"
    )


def _add_entries(conn: sqlite3.Connection, pid: int, entries: list[dict[str, Any]], *, queue: bool) -> int:
    present = {r[0] for r in conn.execute("SELECT video_id FROM playlist_items WHERE playlist_id=?", (pid,))}
    pos = _next_position(conn, pid)
    now = db.now_iso()
    added = 0
    for e in entries:
        if e["id"] in present or len(present) >= MAX_ITEMS:
            continue
        _ensure_video(conn, e, queue=queue)
        conn.execute(
            "INSERT INTO playlist_items(playlist_id, video_id, position, added_at) VALUES(?,?,?,?)",
            (pid, e["id"], pos, now),
        )
        present.add(e["id"])
        pos += 1
        added += 1
    return added


def _states(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        out.update(
            conn.execute(
                f"SELECT id, state FROM videos WHERE id IN ({','.join('?' * len(chunk))})", chunk
            ).fetchall()
        )
    return out


def youtube_preview(conn: sqlite3.Connection, listing: dict[str, Any]) -> dict[str, Any]:
    """What importing this YouTube playlist would bring in (also used by /api/resolve)."""
    entries = listing["entries"]
    states = _states(conn, [e["id"] for e in entries])
    existing = conn.execute(
        "SELECT id FROM playlists WHERE source_id = ? ORDER BY id LIMIT 1", (listing["id"],)
    ).fetchone()
    return {
        "id": listing["id"],
        "title": listing["title"],
        "owner": listing["owner"],
        "description": listing["description"],
        "count": len(entries),
        "cached": sum(1 for e in entries if states.get(e["id"]) == "cached"),
        "truncated": listing["truncated"],
        "imported_as": existing["id"] if existing else None,
        "items": [
            {
                "id": e["id"],
                "title": e.get("title"),
                "duration_s": int(e["duration"]) if e.get("duration") else None,
                "channel": {"id": e.get("channel_id"), "title": e.get("channel")},
                "thumb": e.get("thumb_url") or f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
                "state": states.get(e["id"]),
            }
            for e in entries[:PREVIEW_ITEMS]
        ],
    }


def _playlist_id_from(body_or_url: str) -> str:
    pl = yt.parse_playlist(body_or_url)
    if pl is None:
        raise ApiError(422, "not_a_playlist", "that doesn't look like a YouTube playlist link", field="url")
    return pl


# ---- YouTube import (declared before /playlists/{pid}) ------------------------------------


@router.get("/playlists/youtube")
async def preview_youtube(url: str, cfg: Config = Depends(get_cfg)) -> dict[str, Any]:
    listing = await yt.playlist_listing(cfg, _playlist_id_from(url))

    def work() -> dict[str, Any]:
        conn = db.connect(cfg.db_path)
        try:
            return youtube_preview(conn, listing)
        finally:
            conn.close()

    return await asyncio.to_thread(work)


@router.post("/playlists/import", status_code=201)
async def import_youtube(
    body: dict[str, Any] = Body(...),
    cfg: Config = Depends(get_cfg),
    profile: int | None = Depends(profile_id),
) -> dict[str, Any]:
    source = _playlist_id_from(str(body.get("url") or ""))
    cache = bool(body.get("cache"))
    title = _title(body["title"]) if body.get("title") else None
    listing = await yt.playlist_listing(cfg, source)

    def work() -> dict[str, Any]:
        conn = db.connect(cfg.db_path)
        try:
            with db.tx(conn):
                if not body.get("again"):
                    existing = conn.execute(
                        "SELECT id FROM playlists WHERE source_id = ?", (source,)
                    ).fetchone()
                    if existing:
                        raise ApiError(
                            409,
                            "already_imported",
                            "that playlist was imported before",
                            playlist_id=existing["id"],
                        )
                owner = (
                    profile
                    if profile and conn.execute("SELECT 1 FROM profiles WHERE id = ?", (profile,)).fetchone()
                    else None
                )
                now = db.now_iso()
                cur = conn.execute(
                    "INSERT INTO playlists(title, description, profile_id, source_id, source_title, source_owner,"
                    " auto_cache, created_at, updated_at, synced_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        title or listing["title"][:120],
                        listing["description"] or None,
                        owner,
                        source,
                        listing["title"],
                        listing["owner"],
                        1 if cache else 0,
                        now,
                        now,
                        now,
                    ),
                )
                pid = int(cur.lastrowid or 0)
                added = _add_entries(conn, pid, listing["entries"], queue=cache)
                db.add_event(
                    conn,
                    "info",
                    "playlist",
                    f"Imported the YouTube playlist “{listing['title']}” ({added} videos)",
                    {"playlist_id": pid, "source_id": source, "cache": cache},
                )
            return {**playlist_json(conn, _get(conn, pid)), "added": added}
        finally:
            conn.close()

    return await asyncio.to_thread(work)


# ---- playlists --------------------------------------------------------------------------


@router.get("/playlists")
def list_playlists(video: str | None = None, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """All playlists, most lately changed first. With `video`, each says whether it holds that video."""
    rows = conn.execute(PLAYLIST_SELECT + " ORDER BY p.updated_at DESC, p.id DESC").fetchall()
    holding: set[int] = set()
    if video:
        check_video_id(video)
        holding = {
            r[0] for r in conn.execute("SELECT playlist_id FROM playlist_items WHERE video_id=?", (video,))
        }
    items = []
    for r in rows:
        p = playlist_json(conn, r)
        if video:
            p["contains"] = r["id"] in holding
        items.append(p)
    return {"items": items}


@router.post("/playlists", status_code=201)
def create_playlist(
    body: dict[str, Any] = Body(...),
    conn: sqlite3.Connection = Depends(get_conn),
    profile: int | None = Depends(profile_id),
) -> dict[str, Any]:
    title = _title(body.get("title"))
    description = _description(body.get("description"))
    ids = [str(v) for v in body.get("video_ids") or []]
    for v in ids:
        check_video_id(v)
    now = db.now_iso()
    with db.tx(conn):
        owner = (
            profile
            if profile and conn.execute("SELECT 1 FROM profiles WHERE id=?", (profile,)).fetchone()
            else None
        )
        cur = conn.execute(
            "INSERT INTO playlists(title, description, profile_id, created_at, updated_at) VALUES(?,?,?,?,?)",
            (title, description, owner, now, now),
        )
        pid = int(cur.lastrowid or 0)
        known = _states(conn, ids)
        _add_entries(conn, pid, [{"id": v} for v in ids if v in known], queue=False)
    return playlist_json(conn, _get(conn, pid))


@router.get("/playlists/{pid}")
def get_playlist(
    pid: int, conn: sqlite3.Connection = Depends(get_conn), profile: int | None = Depends(profile_id)
) -> dict[str, Any]:
    out = playlist_json(conn, _get(conn, pid))
    rows = conn.execute(
        video_select(True)
        + " JOIN playlist_items i ON i.video_id = v.id WHERE i.playlist_id = :pid ORDER BY i.position, i.added_at",
        {"pid": pid, "profile": profile},
    ).fetchall()
    out["items"] = [video_json(r) for r in rows]
    return out


@router.patch("/playlists/{pid}")
def update_playlist(
    pid: int, body: dict[str, Any] = Body(...), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    _get(conn, pid)
    sets: dict[str, Any] = {}
    if "title" in body:
        sets["title"] = _title(body["title"])
    if "description" in body:
        sets["description"] = _description(body["description"])
    if "auto_cache" in body:
        sets["auto_cache"] = 1 if body["auto_cache"] else 0
    if sets:
        sets["updated_at"] = db.now_iso()
        conn.execute(
            f"UPDATE playlists SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), pid)
        )
    return playlist_json(conn, _get(conn, pid))


@router.delete("/playlists/{pid}")
def delete_playlist(pid: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Deletes the list only; cached videos stay in the library and are evicted as usual."""
    with db.tx(conn):
        if not conn.execute("DELETE FROM playlists WHERE id=?", (pid,)).rowcount:
            raise ApiError(404, "not_found", "no such playlist")
        _prune_orphans(conn)
    return {"ok": True}


@router.post("/playlists/{pid}/items")
def add_item(
    pid: int, body: dict[str, Any] = Body(...), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    """Append a video. Unknown videos need their particulars in the body (title, channel, duration_s,
    thumb), as from search results; they are remembered without being cached."""
    vid = check_video_id(str(body.get("video_id") or ""))
    _get(conn, pid)
    with db.tx(conn):
        known = conn.execute("SELECT 1 FROM videos WHERE id=?", (vid,)).fetchone() is not None
        if not known and not body.get("title"):
            raise ApiError(404, "not_found", "video not known; send its title with it")
        added = _add_entries(conn, pid, [{**body, "id": vid}], queue=False)
        if added:
            _touch(conn, pid)
    return {"ok": True, "added": bool(added), "playlist": playlist_json(conn, _get(conn, pid))}


@router.delete("/playlists/{pid}/items/{video_id}")
def remove_item(pid: int, video_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    check_video_id(video_id)
    _get(conn, pid)
    with db.tx(conn):
        gone = conn.execute(
            "DELETE FROM playlist_items WHERE playlist_id=? AND video_id=?", (pid, video_id)
        ).rowcount
        if gone:
            _touch(conn, pid)
            _prune_orphans(conn)
    return {"ok": True, "removed": bool(gone), "playlist": playlist_json(conn, _get(conn, pid))}


@router.put("/playlists/{pid}/order")
def reorder(
    pid: int, body: dict[str, Any] = Body(...), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    """New order: the given IDs first, in that order; any items not named keep their order after them."""
    want = [str(v) for v in body.get("video_ids") or [] if VIDEO_ID.match(str(v))]
    _get(conn, pid)
    with db.tx(conn):
        current = [
            r[0]
            for r in conn.execute(
                "SELECT video_id FROM playlist_items WHERE playlist_id=? ORDER BY position, added_at", (pid,)
            )
        ]
        have = set(current)
        head = list(dict.fromkeys(v for v in want if v in have))
        named = set(head)
        order = head + [v for v in current if v not in named]
        conn.executemany(
            "UPDATE playlist_items SET position=? WHERE playlist_id=? AND video_id=?",
            [(i, pid, v) for i, v in enumerate(order)],
        )
        _touch(conn, pid)
    return {"ok": True, "video_ids": order}


@router.post("/playlists/{pid}/cache", status_code=202)
def cache_all(
    pid: int, body: dict[str, Any] | None = Body(default=None), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    """Queue every video of the playlist that isn't cached (or on its way)."""
    _get(conn, pid)
    max_height = (body or {}).get("max_height")
    if max_height is not None and (not isinstance(max_height, int) or max_height < 144):
        raise ApiError(422, "bad_max_height", "max_height must be an integer ≥ 144")
    with db.tx(conn):
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT v.id FROM playlist_items i JOIN videos v ON v.id = i.video_id WHERE i.playlist_id=?"
                f" AND v.state IN ({','.join('?' * len(CACHEABLE_FROM))}) ORDER BY i.position",
                (pid, *CACHEABLE_FROM),
            )
        ]
        _requeue(conn, ids, max_height)
    return {"ok": True, "queued": len(ids)}


@router.post("/playlists/{pid}/sync")
async def sync(pid: int, cfg: Config = Depends(get_cfg)) -> dict[str, Any]:
    """Fetch the YouTube playlist anew and append what was added there since. Nothing is removed."""

    def source() -> sqlite3.Row:
        conn = db.connect(cfg.db_path)
        try:
            return _get(conn, pid)
        finally:
            conn.close()

    row = await asyncio.to_thread(source)
    if not row["source_id"]:
        raise ApiError(409, "not_imported", "this playlist was not imported from YouTube")
    listing = await yt.playlist_listing(cfg, row["source_id"], fresh=True)

    def work() -> dict[str, Any]:
        conn = db.connect(cfg.db_path)
        try:
            with db.tx(conn):
                added = _add_entries(conn, pid, listing["entries"], queue=bool(row["auto_cache"]))
                conn.execute(
                    "UPDATE playlists SET synced_at=?, source_title=?, source_owner=?"
                    + (", updated_at=?" if added else "")
                    + " WHERE id=?",
                    (
                        db.now_iso(),
                        listing["title"],
                        listing["owner"],
                        *([db.now_iso()] if added else []),
                        pid,
                    ),
                )
            return {"ok": True, "added": added, "playlist": playlist_json(conn, _get(conn, pid))}
        finally:
            conn.close()

    return await asyncio.to_thread(work)
