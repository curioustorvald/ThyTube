"""Subscriptions (incl. filter preview), queue and storage."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends

from ... import db, discovery, filters
from ...config import Config
from ...downloader import PROGRESS_KEY
from ...pool import Pool
from .. import yt
from ..common import (
    CHANNEL_ID,
    ApiError,
    check_video_id,
    get_cfg,
    get_conn,
    subscription_json,
    video_json,
    video_select,
)

router = APIRouter()

RULE_FIELDS = {
    "include_regex": (str, type(None)),
    "exclude_regex": (str, type(None)),
    "case_sensitive": (bool,),
    "min_duration_s": (int, type(None)),
    "max_duration_s": (int, type(None)),
    "include_shorts": (bool,),
    "include_streams": (bool,),
    "backfill_count": (int,),
    "max_height": (int, type(None)),
    "enabled": (bool,),
}


def _clean_rules(body: dict[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, types in RULE_FIELDS.items():
        if k not in body:
            continue
        v = body[k]
        if isinstance(v, str) and k.endswith("_regex"):
            v = v.strip() or None
        if v is None:
            ok = type(None) in types
        elif bool in types:
            ok = isinstance(v, bool)
        elif int in types:
            ok = isinstance(v, int) and not isinstance(v, bool)
        else:
            ok = isinstance(v, str)
        if not ok:
            raise ApiError(422, "bad_field", f"{k} has the wrong type", field=k)
        out[k] = v
    case = bool(out.get("case_sensitive", body.get("case_sensitive", False)))
    for k in ("include_regex", "exclude_regex"):
        err = filters.validate_regex(out.get(k), case)
        if err:
            label = "Include" if k.startswith("include") else "Exclude"
            raise ApiError(422, "invalid_regex", f"{label} pattern: {err}", field=k)
    for k in ("min_duration_s", "max_duration_s"):
        if out.get(k) is not None and out[k] < 0:
            raise ApiError(422, "bad_field", f"{k} must not be negative", field=k)
    if (
        out.get("min_duration_s")
        and out.get("max_duration_s")
        and out["min_duration_s"] > out["max_duration_s"]
    ):
        raise ApiError(422, "bad_field", "minimum duration is above the maximum", field="min_duration_s")
    if "backfill_count" in out and not 0 <= out["backfill_count"] <= 500:
        raise ApiError(422, "bad_field", "backfill must be between 0 and 500", field="backfill_count")
    if out.get("max_height") is not None and out["max_height"] < 144:
        raise ApiError(422, "bad_field", "quality cap must be at least 144p", field="max_height")
    return out


SUB_SELECT = (
    "SELECT s.*, c.title AS channel_title, c.handle AS channel_handle, c.avatar_path AS channel_avatar_path,"
    " c.avatar_url AS channel_avatar_url,"
    " (SELECT COUNT(*) FROM videos v WHERE v.subscription_id=s.id AND v.state='cached') AS cached_count,"
    " (SELECT COALESCE(SUM(size_bytes),0) FROM videos v WHERE v.subscription_id=s.id AND v.state='cached') AS cached_bytes,"
    " (SELECT COUNT(*) FROM videos v WHERE v.subscription_id=s.id AND v.state IN ('queued','downloading')) AS queued_count"
    " FROM subscriptions s LEFT JOIN channels c ON c.id = s.channel_id"
)


def _get_sub(conn: sqlite3.Connection, sub_id: int) -> sqlite3.Row:
    row = conn.execute(SUB_SELECT + " WHERE s.id=?", (sub_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "no such subscription")
    return row


@router.get("/subscriptions")
def list_subscriptions(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    rows = conn.execute(SUB_SELECT + " ORDER BY LOWER(c.title), s.id").fetchall()
    return {"items": [subscription_json(r) for r in rows]}


@router.post("/subscriptions", status_code=201)
def create_subscription(
    body: dict[str, Any] = Body(...), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    channel = body.get("channel") or {}
    cid = body.get("channel_id") or channel.get("id")
    if not cid or not CHANNEL_ID.match(cid):
        raise ApiError(422, "bad_channel", "a channel ID (UC…) is required", field="channel_id")
    rules = _clean_rules(body, partial=False)
    now = db.now_iso()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO channels(id, title, handle, avatar_url, updated_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET title=COALESCE(excluded.title, title),"
            " handle=COALESCE(excluded.handle, handle), avatar_url=COALESCE(excluded.avatar_url, avatar_url)",
            (cid, channel.get("title"), channel.get("handle"), channel.get("avatar"), now),
        )
        cols = ["channel_id", "created_at", *rules]
        cur = conn.execute(
            f"INSERT INTO subscriptions({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
            (cid, now, *[int(v) if isinstance(v, bool) else v for v in rules.values()]),
        )
        sub_id = int(cur.lastrowid or 0)
        db.enqueue_command(conn, "poll_subscription", {"id": sub_id, "backfill": True})
        db.add_event(
            conn, "info", "subscription", f"Subscribed to {channel.get('title') or cid}", {"id": sub_id}
        )
    return subscription_json(_get_sub(conn, sub_id))


@router.patch("/subscriptions/{sub_id}")
def update_subscription(
    sub_id: int, body: dict[str, Any] = Body(...), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    _get_sub(conn, sub_id)
    rules = _clean_rules(body, partial=True)
    if rules:
        sets = ", ".join(f"{k}=?" for k in rules)
        conn.execute(
            f"UPDATE subscriptions SET {sets} WHERE id=?",
            (*[int(v) if isinstance(v, bool) else v for v in rules.values()], sub_id),
        )
        if "max_height" in rules:
            conn.execute(
                "UPDATE videos SET max_height=? WHERE subscription_id=? AND state='queued'",
                (rules["max_height"], sub_id),
            )
    return subscription_json(_get_sub(conn, sub_id))


@router.delete("/subscriptions/{sub_id}")
def delete_subscription(
    sub_id: int, delete_files: bool = False, conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    sub = _get_sub(conn, sub_id)
    with db.tx(conn):
        ids: list[str] = []
        if delete_files:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM videos WHERE subscription_id=? AND state='cached' AND pinned=0 AND origin='subscription'",
                    (sub_id,),
                )
            ]
            if ids:
                db.enqueue_command(conn, "delete_files", {"video_ids": ids, "reason": "subscription deleted"})
        conn.execute(
            "UPDATE videos SET state='filtered', filter_reason='subscription removed'"
            " WHERE subscription_id=? AND state IN ('queued','deferred_space')",
            (sub_id,),
        )
        downloading = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM videos WHERE subscription_id=? AND state='downloading'", (sub_id,)
            )
        ]
        for vid in downloading:
            db.enqueue_command(conn, "cancel_download", {"video_id": vid})
        conn.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
        db.add_event(
            conn,
            "info",
            "subscription",
            f"Unsubscribed from {sub['channel_title'] or sub['channel_id']}"
            + (f"; deleting {len(ids)} cached video(s)" if ids else ""),
            {"id": sub_id},
        )
    return {"ok": True, "deleting": len(ids)}


@router.post("/subscriptions/{sub_id}/poll", status_code=202)
def poll_now(sub_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    _get_sub(conn, sub_id)
    return {"ok": True, "command_id": db.enqueue_command(conn, "poll_subscription", {"id": sub_id})}


@router.post("/subscriptions/preview")
async def preview(body: dict[str, Any] = Body(...), cfg: Config = Depends(get_cfg)) -> dict[str, Any]:
    cid = body.get("channel_id")
    if not cid or not CHANNEL_ID.match(cid):
        raise ApiError(422, "bad_channel", "a channel ID (UC…) is required", field="channel_id")
    rules = _clean_rules(body, partial=True)
    entries, meta = await yt.channel_listing(cfg, cid, "videos", cfg.discovery.flat_listing_limit)
    if rules.get("include_shorts"):
        try:
            shorts, _ = await yt.channel_listing(cfg, cid, "shorts", 15)
            entries = entries + shorts
        except ApiError:
            pass
    items = discovery.preview(rules, entries)
    return {
        "channel": meta,
        "items": items,
        "matched": sum(1 for i in items if i["match"]),
        "total": len(items),
        "note": "Duration, shorts and live checks are re-applied with full metadata at download time.",
    }


# ---- queue ------------------------------------------------------------------------------


@router.get("/queue")
def queue(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    progress = db.kv_get(conn, PROGRESS_KEY) or {}
    sel = video_select(False)

    def rows(where: str, order: str, limit: int = 200) -> list[dict[str, Any]]:
        return [
            video_json(r)
            for r in conn.execute(f"{sel} WHERE {where} ORDER BY {order} LIMIT {limit}").fetchall()
        ]

    active = rows("v.state='downloading'", "v.queued_at")
    for a in active:
        a["progress"] = progress.get(a["id"])
    return {
        "active": active,
        "queued": rows(
            "v.state='queued'", "v.origin='manual' DESC, v.next_attempt_at IS NOT NULL, v.queued_at"
        ),
        "deferred": rows("v.state='deferred_space'", "v.queued_at"),
        "failed": rows("v.state='failed'", "v.queued_at DESC", 100),
        "unavailable": rows("v.state='unavailable'", "v.discovered_at DESC", 50),
    }


@router.post("/queue/{video_id}/retry")
def retry(video_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    check_video_id(video_id)
    n = conn.execute(
        "UPDATE videos SET state='queued', attempts=0, systemic_failures=0, next_attempt_at=NULL, queued_at=?"
        " WHERE id=? AND state IN ('queued','failed','unavailable','deferred_space','evicted','filtered')",
        (db.now_iso(), video_id),
    ).rowcount
    if not n:
        raise ApiError(409, "not_retryable", "this video can't be retried in its current state")
    return {"ok": True}


@router.delete("/queue/{video_id}")
def cancel(video_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    check_video_id(video_id)
    n = conn.execute(
        "UPDATE videos SET state='filtered', filter_reason='cancelled' WHERE id=? AND state IN ('queued','deferred_space','failed')",
        (video_id,),
    ).rowcount
    if n:
        return {"ok": True}
    row = conn.execute("SELECT state FROM videos WHERE id=?", (video_id,)).fetchone()
    if row and row["state"] == "downloading":
        db.enqueue_command(conn, "cancel_download", {"video_id": video_id})
        return {"ok": True, "pending": True}
    raise ApiError(409, "not_cancellable", "nothing to cancel")


# ---- storage ------------------------------------------------------------------------------


@router.get("/storage")
def storage(conn: sqlite3.Connection = Depends(get_conn), cfg: Config = Depends(get_cfg)) -> dict[str, Any]:
    pool = Pool(cfg)
    per_channel = conn.execute(
        "SELECT v.channel_id, c.title, COUNT(*) AS count, SUM(v.size_bytes) AS bytes,"
        " SUM(CASE WHEN v.pinned=1 THEN v.size_bytes ELSE 0 END) AS pinned_bytes"
        " FROM videos v LEFT JOIN channels c ON c.id=v.channel_id WHERE v.state='cached'"
        " GROUP BY v.channel_id ORDER BY bytes DESC"
    ).fetchall()
    preview = pool.eviction_candidates(conn, limit=20)
    preview_items = []
    for r in preview:
        item = video_json(r)
        item["protected"] = bool(r["protected"])
        preview_items.append(item)
    return {
        "usage": pool.usage(conn),
        "per_channel": [dict(r) for r in per_channel],
        "eviction_preview": preview_items,
        "grace_days": cfg.eviction.grace_days,
    }
