"""The media pool: layout, quota admission, reservations, eviction and reconciliation.

Invariant (the worker is the only pool writer and holds ``Pool.lock`` while checking it):

    Σ size_bytes(cached) + Σ reservations ≤ pool.max_bytes
    and filesystem free space after the download ≥ pool.min_free_bytes
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db
from .config import Config

log = logging.getLogger(__name__)

# A video someone is watching right now is never evicted (seconds since the last heartbeat).
PLAYING_GRACE_S = 300


def media_rel(video_id: str) -> str:
    return f"media/{video_id[:2]}/{video_id}"


def avatar_rel(channel_id: str) -> str:
    return f"channels/{channel_id}/avatar.jpg"


def url_for(rel: str | None) -> str | None:
    """Public URL for a pool-relative path (Apache aliases /media and /avatars)."""
    if not rel:
        return None
    if rel.startswith("media/"):
        return "/" + rel
    if rel.startswith("channels/"):
        return "/avatars/" + rel[len("channels/") :]
    return None


@dataclass
class Admission:
    ok: bool
    evicted: list[str]
    reason: str = ""


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


class Pool:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.lock = threading.RLock()

    # ---- paths -----------------------------------------------------------

    def media_dir(self, video_id: str) -> Path:
        return self.cfg.pool_dir / media_rel(video_id)

    def partial_dir(self, video_id: str) -> Path:
        return self.cfg.partial_dir / video_id

    # ---- accounting ------------------------------------------------------

    def fs_free(self) -> int:
        return shutil.disk_usage(self.cfg.pool_dir).free

    def usage(self, conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN state='cached' THEN size_bytes END),0) AS cached,"
            " COALESCE(SUM(CASE WHEN state='cached' AND pinned=1 THEN size_bytes END),0) AS pinned,"
            " COALESCE(SUM(reserved_bytes),0) AS reserved,"
            " COUNT(CASE WHEN state='cached' THEN 1 END) AS cached_count"
            " FROM videos"
        ).fetchone()
        try:
            du = shutil.disk_usage(self.cfg.pool_dir)
            fs = {"fs_total": du.total, "fs_free": du.free}
        except OSError:
            fs = {"fs_total": None, "fs_free": None}
        return {
            "max_bytes": self.cfg.pool.max_bytes,
            "min_free_bytes": self.cfg.pool.min_free_bytes,
            "cached_bytes": row["cached"],
            "cached_count": row["cached_count"],
            "pinned_bytes": row["pinned"],
            "pinned_limit": int(self.cfg.pool.max_bytes * self.cfg.pool.max_pinned_ratio),
            "reserved_bytes": row["reserved"],
            **fs,
        }

    def _headroom(self, conn: sqlite3.Connection, exclude_video: str | None = None) -> tuple[int, int]:
        """(budget headroom, filesystem headroom) available for a new reservation."""
        u = self.usage(conn)
        reserved = u["reserved_bytes"]
        if exclude_video:
            own = conn.execute("SELECT reserved_bytes FROM videos WHERE id=?", (exclude_video,)).fetchone()
            reserved -= (own["reserved_bytes"] or 0) if own else 0
        budget = self.cfg.pool.max_bytes - u["cached_bytes"] - reserved
        fs_room = self.fs_free() - self.cfg.pool.min_free_bytes - reserved
        return budget, fs_room

    # ---- eviction --------------------------------------------------------

    def eviction_candidates(self, conn: sqlite3.Connection, limit: int = 1000) -> list[sqlite3.Row]:
        """Cached, unpinned videos in eviction order: least rewatched first, then oldest activity,
        then biggest. Unwatched downloads within the grace period come last (oldest first)."""
        grace_cutoff = db.in_iso(days=-self.cfg.eviction.grace_days)
        playing_cutoff = db.in_iso(seconds=-PLAYING_GRACE_S)
        return conn.execute(
            """
            SELECT v.*, c.title AS channel_title,
                   (v.total_watch_count = 0 AND v.downloaded_at > :grace) AS protected
            FROM videos v LEFT JOIN channels c ON c.id = v.channel_id
            WHERE v.state = 'cached' AND v.pinned = 0
              AND v.id NOT IN (SELECT video_id FROM play_sessions WHERE last_heartbeat_at > :playing)
            ORDER BY protected ASC,
                     CASE WHEN protected THEN v.downloaded_at END ASC,
                     v.total_watch_count ASC,
                     COALESCE(v.last_watched_at, v.downloaded_at) ASC,
                     v.size_bytes DESC
            LIMIT :limit
            """,
            {"grace": grace_cutoff, "playing": playing_cutoff, "limit": limit},
        ).fetchall()

    def _remove_media(self, video_id: str) -> None:
        d = self.media_dir(video_id)
        if not d.exists():
            return
        for child in d.iterdir():
            if child.name == "thumb.jpg":
                continue  # thumbnails of evicted videos are kept for the library
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    def evict(self, conn: sqlite3.Connection, video_id: str, reason: str) -> None:
        with self.lock:
            row = conn.execute("SELECT title, size_bytes FROM videos WHERE id=?", (video_id,)).fetchone()
            self._remove_media(video_id)
            with db.tx(conn):
                conn.execute(
                    "UPDATE videos SET state='evicted', evicted_at=?, file_path=NULL, reserved_bytes=NULL,"
                    " subs_state=NULL WHERE id=?",
                    (db.now_iso(), video_id),
                )
                conn.execute("DELETE FROM subtitles WHERE video_id=?", (video_id,))
                db.add_event(
                    conn,
                    "info",
                    "evicted",
                    f"Removed “{row['title'] if row else video_id}” ({reason})",
                    {"video_id": video_id, "reason": reason, "bytes": row["size_bytes"] if row else None},
                )

    def admit(self, conn: sqlite3.Connection, video_id: str, need: int) -> Admission:
        """Reserve ``need`` bytes for ``video_id`` (replacing any existing reservation), evicting
        as required. Evicts nothing unless the reservation is then guaranteed to fit."""
        with self.lock:
            budget, fs_room = self._headroom(conn, exclude_video=video_id)
            if need > self.cfg.pool.max_bytes:
                return Admission(False, [], "larger than the whole pool budget")
            shortfall = max(need - budget, need - fs_room, 0)
            evict: list[str] = []
            if shortfall > 0:
                freed = 0
                for cand in self.eviction_candidates(conn):
                    if cand["id"] == video_id:
                        continue
                    evict.append(cand["id"])
                    freed += cand["size_bytes"] or 0
                    if freed >= shortfall:
                        break
                if freed < shortfall:
                    return Admission(False, [], "not enough evictable space")
                for vid in evict:
                    self.evict(conn, vid, "making room")
                budget, fs_room = self._headroom(conn, exclude_video=video_id)
                if need > budget or need > fs_room:
                    # The filesystem didn't free as much as size_bytes promised (shared disk).
                    return Admission(False, evict, "filesystem space did not free up as expected")
            conn.execute("UPDATE videos SET reserved_bytes=? WHERE id=?", (need, video_id))
            return Admission(True, evict)

    def release(self, conn: sqlite3.Connection, video_id: str) -> None:
        with self.lock:
            conn.execute("UPDATE videos SET reserved_bytes=NULL WHERE id=?", (video_id,))

    def partial_size(self, video_id: str) -> int:
        return _dir_size(self.partial_dir(video_id))

    # ---- finalising downloads ------------------------------------------

    def finalize(self, video_id: str, files: dict[str, Path]) -> Path:
        """Atomically move finished files (same filesystem) into the media dir.
        ``files`` maps target names (``video.mp4``, ``thumb.jpg``) to sources in .partial."""
        target = self.media_dir(video_id)
        with self.lock:
            target.mkdir(parents=True, exist_ok=True)
            for name, src in files.items():
                os.replace(src, target / name)
        return target

    # ---- user actions ---------------------------------------------------

    def pinned_bytes(self, conn: sqlite3.Connection) -> int:
        return conn.execute(
            "SELECT COALESCE(SUM(size_bytes),0) FROM videos WHERE state='cached' AND pinned=1"
        ).fetchone()[0]

    def can_pin(self, conn: sqlite3.Connection, video_id: str) -> bool:
        row = conn.execute("SELECT size_bytes, est_bytes FROM videos WHERE id=?", (video_id,)).fetchone()
        size = (row["size_bytes"] or row["est_bytes"] or 0) if row else 0
        limit = self.cfg.pool.max_bytes * self.cfg.pool.max_pinned_ratio
        return self.pinned_bytes(conn) + size <= limit

    # ---- reconcile -------------------------------------------------------

    def reconcile(self, conn: sqlite3.Connection, active: set[str] | None = None) -> dict[str, Any]:
        """Make the DB agree with the disk. ``active`` = video IDs currently downloading (skipped)."""
        active = active or set()
        self.cfg.ensure_pool_dirs()
        report: dict[str, Any] = {"missing": [], "orphans": [], "stale_partials": [], "resized": []}
        with self.lock:
            rows = conn.execute(
                "SELECT id, file_path, size_bytes, state FROM videos WHERE state='cached'"
            ).fetchall()
            known_dirs: set[str] = set()
            for r in rows:
                known_dirs.add(r["id"])
                f = self.cfg.pool_dir / r["file_path"] if r["file_path"] else None
                if f is None or not f.is_file():
                    report["missing"].append(r["id"])
                    with db.tx(conn):
                        conn.execute(
                            "UPDATE videos SET state='evicted', evicted_at=?, file_path=NULL WHERE id=?",
                            (db.now_iso(), r["id"]),
                        )
                        conn.execute("DELETE FROM subtitles WHERE video_id=?", (r["id"],))
                    continue
                size = f.stat().st_size
                if size != r["size_bytes"]:
                    report["resized"].append(r["id"])
                    conn.execute("UPDATE videos SET size_bytes=? WHERE id=?", (size, r["id"]))

            # Directories under media/ for IDs that are not cached: keep thumbnails of known videos,
            # remove everything else.
            all_ids = {r[0] for r in conn.execute("SELECT id FROM videos")}
            for prefix in self.cfg.media_dir.iterdir() if self.cfg.media_dir.exists() else []:
                if not prefix.is_dir():
                    continue
                for d in prefix.iterdir():
                    vid = d.name
                    if vid in known_dirs or vid in active:
                        continue
                    if vid in all_ids:
                        leftovers = [c for c in d.iterdir() if c.name != "thumb.jpg"]
                        if leftovers:
                            report["orphans"].append(vid)
                            self._remove_media(vid)
                    else:
                        report["orphans"].append(vid)
                        shutil.rmtree(d, ignore_errors=True)
                if prefix.exists() and not any(prefix.iterdir()):
                    prefix.rmdir()

            for d in self.cfg.partial_dir.iterdir() if self.cfg.partial_dir.exists() else []:
                if d.name in active:
                    continue
                report["stale_partials"].append(d.name)
                shutil.rmtree(d, ignore_errors=True)

            # Subtitle rows whose files vanished.
            for s in conn.execute("SELECT video_id, lang, kind, path FROM subtitles").fetchall():
                if not (self.cfg.pool_dir / s["path"]).is_file():
                    conn.execute(
                        "DELETE FROM subtitles WHERE video_id=? AND lang=? AND kind=?",
                        (s["video_id"], s["lang"], s["kind"]),
                    )
            if any(report[k] for k in report):
                db.add_event(
                    conn,
                    "warn" if report["missing"] else "info",
                    "reconcile",
                    f"Reconciled pool: {len(report['missing'])} missing, {len(report['orphans'])} orphan dirs, "
                    f"{len(report['stale_partials'])} stale partials, {len(report['resized'])} resized",
                    report,
                )
            db.kv_set(conn, "last_reconcile", {"at": db.now_iso(), **{k: len(v) for k, v in report.items()}})
        return report
