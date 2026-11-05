"""Watch accounting (heartbeats → watch counts) and the next-video pick."""

from __future__ import annotations

import random
import sqlite3
from typing import Any

from . import db
from .config import Config

MAX_DELTA_S = 120.0  # a heartbeat every 15 s, at up to 2× speed, with slack for missed beats


def threshold(cfg: Config, duration: float | None) -> float:
    d = duration or 0
    if d <= 0:
        return float(cfg.watch.max_seconds)
    return min(cfg.watch.ratio * d, float(cfg.watch.max_seconds))


def heartbeat(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    session_id: str,
    video_id: str,
    profile_id: int | None,
    position: float,
    played_delta: float,
) -> dict[str, Any]:
    now = db.now_iso()
    delta = max(0.0, min(float(played_delta or 0), MAX_DELTA_S))
    with db.tx(conn):
        video = conn.execute("SELECT duration_s FROM videos WHERE id=?", (video_id,)).fetchone()
        if video is None:
            return {"ok": False, "error": "unknown video"}
        if (
            profile_id is not None
            and not conn.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone()
        ):
            profile_id = None
        sess = conn.execute("SELECT * FROM play_sessions WHERE id=?", (session_id,)).fetchone()
        if sess is None:
            conn.execute(
                "INSERT INTO play_sessions(id, profile_id, video_id, started_at, last_heartbeat_at, played_s)"
                " VALUES(?,?,?,?,?,?)",
                (session_id, profile_id, video_id, now, now, delta),
            )
            played, counted = delta, 0
        else:
            if sess["video_id"] != video_id:
                return {"ok": False, "error": "session belongs to another video"}
            played, counted = sess["played_s"] + delta, sess["counted"]
            conn.execute(
                "UPDATE play_sessions SET played_s=?, last_heartbeat_at=? WHERE id=?",
                (played, now, session_id),
            )

        duration = video["duration_s"]
        pos = max(0.0, float(position or 0))
        if duration and pos >= duration * 0.95:
            pos = 0.0  # finished: resume from the start next time
        newly_counted = False
        if not counted and played >= threshold(cfg, duration):
            counted, newly_counted = 1, True
            conn.execute("UPDATE play_sessions SET counted=1 WHERE id=?", (session_id,))
            conn.execute(
                "UPDATE videos SET total_watch_count=total_watch_count+1, last_watched_at=? WHERE id=?",
                (now, video_id),
            )
        else:
            conn.execute("UPDATE videos SET last_watched_at=? WHERE id=?", (now, video_id))

        if profile_id is not None:
            conn.execute(
                "INSERT INTO watch_stats(profile_id, video_id, watch_count, position_s, last_watched_at)"
                " VALUES(?,?,?,?,?) ON CONFLICT(profile_id, video_id) DO UPDATE SET"
                " watch_count=watch_count+excluded.watch_count, position_s=excluded.position_s,"
                " last_watched_at=excluded.last_watched_at",
                (profile_id, video_id, 1 if newly_counted else 0, pos, now),
            )
    return {
        "ok": True,
        "played_s": round(played, 1),
        "counted": bool(counted),
        "newly_counted": newly_counted,
    }


def prune_sessions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM play_sessions WHERE last_heartbeat_at < ?", (db.in_iso(days=-30),))


def pick_next(
    conn: sqlite3.Connection,
    video_id: str,
    profile_id: int | None,
    exclude: list[str],
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    """Channel-random: a cached video from the same channel, not the current one and not already
    played this session (reset once exhausted). Videos whose watch count for this profile equals the
    minimum among the candidates get weight 2, the rest weight 1."""
    rng = rng or random.Random()
    cur = conn.execute("SELECT channel_id FROM videos WHERE id=?", (video_id,)).fetchone()
    if cur is None or cur["channel_id"] is None:
        return None
    rows = conn.execute(
        "SELECT v.id, COALESCE(w.watch_count, 0) AS wc FROM videos v"
        " LEFT JOIN watch_stats w ON w.video_id = v.id AND w.profile_id = ?"
        " WHERE v.channel_id=? AND v.state='cached' AND v.id != ?",
        (profile_id, cur["channel_id"], video_id),
    ).fetchall()
    if not rows:
        return None
    excluded = set(exclude)
    candidates = [r for r in rows if r["id"] not in excluded]
    reset = False
    if not candidates:
        candidates, reset = list(rows), True
    least = min(r["wc"] for r in candidates)
    weights = [2 if r["wc"] == least else 1 for r in candidates]
    choice = rng.choices(candidates, weights=weights, k=1)[0]
    return {"id": choice["id"], "reset": reset, "candidates": len(candidates)}
