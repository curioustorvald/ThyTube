"""Status, events, admin actions, profiles, search/resolve and playback."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, Request

from ... import db, playback
from ...config import Config
from ...downloader import PROGRESS_KEY
from ...health import Health, explain
from ...pool import Pool
from .. import yt
from ..common import ApiError, check_video_id, get_cfg, get_conn, profile_id, video_json, video_select

router = APIRouter()

WORKER_STALE_S = 30


# ---- status --------------------------------------------------------------------------


@router.get("/status")
def status(conn: sqlite3.Connection = Depends(get_conn), cfg: Config = Depends(get_cfg)) -> dict[str, Any]:
    h = Health(cfg).get(conn)
    h["explanation"] = explain(h)
    counts = {
        r["state"]: r["n"] for r in conn.execute("SELECT state, COUNT(*) AS n FROM videos GROUP BY state")
    }
    hb = db.kv_get(conn, "worker_heartbeat") or {}
    hb_at = db.parse_iso(hb.get("at"))
    alive = hb_at is not None and (db.now() - hb_at).total_seconds() < WORKER_STALE_S
    ytdlp = db.kv_get(conn, "ytdlp") or {}
    return {
        "health": h,
        "ytdlp": {
            "version": ytdlp.get("version"),
            "channel": cfg.ytdlp.channel,
            "last_update": ytdlp.get("last_update"),
            "history": (ytdlp.get("history") or [])[:10],
        },
        "queue": counts,
        "pool": Pool(cfg).usage(conn),
        "worker": {
            "alive": alive,
            "heartbeat_at": hb.get("at"),
            "active": hb.get("active", []),
            "task": hb.get("bg"),
        },
        "progress": db.kv_get(conn, PROGRESS_KEY) or {},
        "server_time": db.now_iso(),
        "cookies_enabled": bool(cfg.ytdlp.cookies),
    }


@router.get("/events")
def events(
    level: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    before: int | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    where, params = [], []
    if level:
        levels = {"info": ["info", "warn", "error"], "warn": ["warn", "error"], "error": ["error"]}.get(
            level, [level]
        )
        where.append(f"level IN ({','.join('?' * len(levels))})")
        params += levels
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if before:
        where.append("id < ?")
        params.append(before)
    sql = (
        "SELECT * FROM events"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY id DESC LIMIT ?"
    )
    rows = conn.execute(sql, (*params, max(1, min(limit, 500)))).fetchall()
    items = [{**dict(r), "data": json.loads(r["data_json"]) if r["data_json"] else None} for r in rows]
    for i in items:
        i.pop("data_json", None)
    return {"items": items, "next_before": items[-1]["id"] if len(items) == limit else None}


@router.post("/admin/probe", status_code=202)
def admin_probe(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    return {"ok": True, "command_id": db.enqueue_command(conn, "probe", {"reason": "manual"})}


@router.post("/admin/update-ytdlp", status_code=202)
def admin_update(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    return {"ok": True, "command_id": db.enqueue_command(conn, "update_ytdlp", {"reason": "manual"})}


@router.post("/admin/rollback-ytdlp", status_code=202)
def admin_rollback(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    return {"ok": True, "command_id": db.enqueue_command(conn, "rollback_ytdlp", {"reason": "manual"})}


@router.post("/admin/reconcile", status_code=202)
def admin_reconcile(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    return {"ok": True, "command_id": db.enqueue_command(conn, "reconcile", {})}


@router.get("/commands/{cmd_id}")
def command_status(cmd_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM commands WHERE id=?", (cmd_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "no such command")
    return {**dict(row), "result": json.loads(row["result_json"]) if row["result_json"] else None}


# ---- profiles --------------------------------------------------------------------------


def _profile_body(body: dict[str, Any], partial: bool) -> dict[str, Any]:
    out = {}
    if "name" in body or not partial:
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 40:
            raise ApiError(422, "bad_name", "name must be 1–40 characters", field="name")
        out["name"] = name
    if "colour" in body:
        colour = str(body["colour"])
        if not (len(colour) == 7 and colour.startswith("#")):
            raise ApiError(422, "bad_colour", "colour must look like #rrggbb", field="colour")
        out["colour"] = colour
    return out


@router.get("/profiles")
def list_profiles(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    return {"items": [dict(r) for r in conn.execute("SELECT * FROM profiles ORDER BY name")]}


@router.post("/profiles", status_code=201)
def create_profile(
    body: dict[str, Any] = Body(...), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    data = _profile_body(body, partial=False)
    try:
        cur = conn.execute(
            "INSERT INTO profiles(name, colour, created_at) VALUES(?,?,?)",
            (data["name"], data.get("colour", "#5b8def"), db.now_iso()),
        )
    except sqlite3.IntegrityError:
        raise ApiError(409, "name_taken", "a profile with that name exists") from None
    return dict(conn.execute("SELECT * FROM profiles WHERE id=?", (cur.lastrowid,)).fetchone())


@router.patch("/profiles/{pid}")
def update_profile(
    pid: int, body: dict[str, Any] = Body(...), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    data = _profile_body(body, partial=True)
    if data:
        try:
            conn.execute(
                f"UPDATE profiles SET {', '.join(f'{k}=?' for k in data)} WHERE id=?", (*data.values(), pid)
            )
        except sqlite3.IntegrityError:
            raise ApiError(409, "name_taken", "a profile with that name exists") from None
    row = conn.execute("SELECT * FROM profiles WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "no such profile")
    return dict(row)


@router.delete("/profiles/{pid}")
def delete_profile(pid: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    if not conn.execute("DELETE FROM profiles WHERE id=?", (pid,)).rowcount:
        raise ApiError(404, "not_found", "no such profile")
    return {"ok": True}


# ---- search / resolve -----------------------------------------------------------------


def _annotate(conn: sqlite3.Connection, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    vids = [r["id"] for r in results if r["type"] == "video"]
    chans = [r["id"] for r in results if r["type"] == "channel"]
    states: dict[str, str] = {}
    if vids:
        states = dict(
            conn.execute(
                f"SELECT id, state FROM videos WHERE id IN ({','.join('?' * len(vids))})", vids
            ).fetchall()
        )
    subscribed: set[str] = set()
    if chans:
        subscribed = {
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT channel_id FROM subscriptions WHERE channel_id IN ({','.join('?' * len(chans))})",
                chans,
            )
        }
    out = []
    for r in results:
        r = dict(r)
        if r["type"] == "video":
            r["state"] = states.get(r["id"])
        else:
            r["subscribed"] = r["id"] in subscribed
        out.append(r)
    return out


@router.get("/search")
async def search(q: str, type: str = "video", cfg: Config = Depends(get_cfg)) -> dict[str, Any]:
    q = q.strip()
    if not q:
        raise ApiError(422, "empty_query", "type something to search for")
    if type not in ("video", "channel"):
        raise ApiError(422, "bad_type", "type must be video or channel")
    results = await yt.search(cfg, q, type)
    conn = db.connect(cfg.db_path)
    try:
        return {"items": _annotate(conn, results)}
    finally:
        conn.close()


@router.get("/resolve")
async def resolve(url: str, cfg: Config = Depends(get_cfg)) -> dict[str, Any]:
    parsed = yt.parse_url(url)
    if parsed is None:
        raise ApiError(422, "not_youtube", "that doesn't look like a YouTube video or channel link")
    kind, value = parsed
    conn = db.connect(cfg.db_path)
    try:
        if kind == "video":
            row = conn.execute(video_select(False) + " WHERE v.id=:id", {"id": value}).fetchone()
            if row is not None and row["title"]:
                return {"type": "video", "video": video_json(row)}
            brief = await yt.video_metadata(cfg, value)
            return {"type": "video", "video": {**brief, "state": row["state"] if row else None}}
        if kind == "channel":
            row = conn.execute("SELECT * FROM channels WHERE id=?", (value,)).fetchone()
            if row is not None and row["title"]:
                meta = {
                    "id": value,
                    "title": row["title"],
                    "handle": row["handle"],
                    "avatar_url": row["avatar_url"],
                }
            else:
                _entries, meta = await yt.channel_listing(cfg, value, "videos", 1)
        else:
            meta = await yt.resolve_channel_url(cfg, value)
        subscribed = (
            conn.execute("SELECT 1 FROM subscriptions WHERE channel_id=?", (meta["id"],)).fetchone()
            is not None
        )
        return {
            "type": "channel",
            "channel": {
                "id": meta["id"],
                "title": meta.get("title"),
                "handle": meta.get("handle"),
                "avatar": meta.get("avatar_url"),
                "subscribed": subscribed,
            },
        }
    finally:
        conn.close()


# ---- playback ---------------------------------------------------------------------------


@router.post("/playback/heartbeat")
async def heartbeat(request: Request, cfg: Config = Depends(get_cfg)) -> dict[str, Any]:
    # Accepts any content type: navigator.sendBeacon can't set headers.
    try:
        body = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        raise ApiError(400, "bad_json", "body must be JSON") from None
    session = str(body.get("session") or "")
    video = str(body.get("video") or "")
    if not (8 <= len(session) <= 64):
        raise ApiError(422, "bad_session", "session id required")
    check_video_id(video)
    profile = body.get("profile")
    if profile is None:
        hdr = request.headers.get("x-profile")
        profile = int(hdr) if hdr and hdr.isdigit() else None
    conn = db.connect(cfg.db_path)
    try:
        return playback.heartbeat(
            conn,
            cfg,
            session_id=session,
            video_id=video,
            profile_id=int(profile) if profile is not None else None,
            position=float(body.get("position") or 0),
            played_delta=float(body.get("played_delta") or 0),
        )
    finally:
        conn.close()


@router.get("/next")
def next_video(
    video: str,
    profile: int | None = None,
    exclude: str = "",
    conn: sqlite3.Connection = Depends(get_conn),
    header_profile: int | None = Depends(profile_id),
) -> dict[str, Any]:
    check_video_id(video)
    pid = profile if profile is not None else header_profile
    pick = playback.pick_next(conn, video, pid, [e for e in exclude.split(",") if e])
    if pick is None:
        return {"video": None}
    row = conn.execute(video_select(True) + " WHERE v.id=:id", {"id": pick["id"], "profile": pid}).fetchone()
    return {"video": video_json(row), "reset": pick["reset"], "candidates": pick["candidates"]}
