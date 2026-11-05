"""Per-video download pipeline (runs in the worker):

metadata → rule checks → admission (reserve, evict) → download → verify → atomic move → subtitles
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import db, filters, subtitles
from .config import Config
from .health import Health
from .pool import Pool, media_rel
from .ytdlp import classify as C
from .ytdlp.runner import Progress, Runner, parse_progress

log = logging.getLogger(__name__)

PROGRESS_KEY = "download_progress"


class Abort(Exception):
    def __init__(self, k: C.Classification) -> None:
        super().__init__(k.message)
        self.k = k


@dataclass
class Outcome:
    state: str
    detail: str = ""


def upsert_channel(conn: sqlite3.Connection, channel_id: str | None, info: dict[str, Any]) -> None:
    if not channel_id:
        return
    title = info.get("channel") or info.get("uploader")
    handle = info.get("uploader_id") if str(info.get("uploader_id") or "").startswith("@") else None
    conn.execute(
        "INSERT INTO channels(id, title, handle, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET title=COALESCE(excluded.title, title),"
        " handle=COALESCE(excluded.handle, handle), updated_at=excluded.updated_at",
        (channel_id, title, handle, db.now_iso()),
    )


def published_iso(info: dict[str, Any]) -> str | None:
    ts = info.get("timestamp") or info.get("release_timestamp")
    if ts:
        return db.iso(datetime.fromtimestamp(int(ts), UTC))
    d = info.get("upload_date")
    if d and len(d) == 8:
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}T00:00:00Z"
    return None


def estimate_bytes(cfg: Config, info: dict[str, Any], max_height: int) -> int | None:
    reqs = info.get("requested_formats") or []
    if reqs:
        sizes = [f.get("filesize") or f.get("filesize_approx") for f in reqs]
        if all(sizes):
            return int(sum(sizes))  # type: ignore[arg-type]
    for key in ("filesize", "filesize_approx"):
        if info.get(key):
            return int(info[key])
    duration = info.get("duration")
    if not duration:
        return None
    ceilings = {int(k): v for k, v in cfg.pool.bitrate_ceiling.items()}
    h = min((k for k in ceilings if k >= max_height), default=max(ceilings))
    return int(duration * ceilings[h] / 8)


def ffprobe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode != 0:
        raise ValueError(f"ffprobe failed: {out.stderr.strip()[-300:]}")
    return json.loads(out.stdout)


def verify(path: Path, expected_duration: float | None) -> dict[str, Any]:
    probe = ffprobe(path)
    streams = probe.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None or a is None:
        raise ValueError("downloaded file lacks a video or an audio stream")
    duration = float(probe.get("format", {}).get("duration") or 0)
    if expected_duration:
        tolerance = max(5.0, expected_duration * 0.03)
        if abs(duration - expected_duration) > tolerance:
            raise ValueError(f"duration {duration:.0f}s differs from expected {expected_duration:.0f}s")
    return {
        "height": v.get("height"),
        "width": v.get("width"),
        "vcodec": v.get("codec_name"),
        "acodec": a.get("codec_name"),
        "duration": duration,
    }


class Downloader:
    def __init__(self, cfg: Config, runner: Runner, pool: Pool, health: Health) -> None:
        self.cfg = cfg
        self.runner = runner
        self.pool = pool
        self.health = health
        self._progress_lock = threading.Lock()

    # ---- progress in kv ---------------------------------------------------

    def _set_progress(self, conn: sqlite3.Connection, video_id: str, data: dict[str, Any] | None) -> None:
        with self._progress_lock:
            all_progress = db.kv_get(conn, PROGRESS_KEY) or {}
            if data is None:
                all_progress.pop(video_id, None)
            else:
                all_progress[video_id] = data
            db.kv_set(conn, PROGRESS_KEY, all_progress)

    # ---- pipeline -----------------------------------------------------------

    def process(self, conn: sqlite3.Connection, video_id: str, cancel: threading.Event) -> Outcome:
        work = self.pool.partial_dir(video_id)
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        try:
            return self._process(conn, video_id, work, cancel)
        except Abort as e:
            return self._fail(conn, video_id, e.k)
        except Exception as e:  # noqa: BLE001
            log.exception("download of %s crashed", video_id)
            return self._fail(conn, video_id, C.Classification(C.UNKNOWN, "exception", repr(e)))
        finally:
            self.pool.release(conn, video_id)
            self._set_progress(conn, video_id, None)
            shutil.rmtree(work, ignore_errors=True)

    def _process(
        self, conn: sqlite3.Connection, video_id: str, work: Path, cancel: threading.Event
    ) -> Outcome:
        row = conn.execute(
            "SELECT v.*, s.include_shorts, s.include_streams, s.min_duration_s, s.max_duration_s,"
            " s.max_height AS sub_max_height, s.id AS sub_id"
            " FROM videos v LEFT JOIN subscriptions s ON s.id = v.subscription_id WHERE v.id=?",
            (video_id,),
        ).fetchone()
        if row is None:
            return Outcome("missing")
        max_height = row["max_height"] or row["sub_max_height"] or self.cfg.ytdlp.max_height
        self._set_progress(conn, video_id, {"stage": "metadata", "started": db.now_iso()})

        # 1. metadata
        res = self.runner.run(
            self.runner.metadata_argv(video_id, max_height),
            timeout=self.cfg.ytdlp.metadata_timeout_s,
            cancel=cancel,
        )
        k = C.classify(res)
        if k.cls != C.OK:
            raise Abort(k)
        try:
            info = json.loads(res.stdout)
        except json.JSONDecodeError:
            raise Abort(C.Classification(C.UNKNOWN, "bad_json", "yt-dlp printed invalid JSON")) from None
        self.health.record(conn, k, "metadata")
        info_path = work / "info.json"
        info_path.write_text(res.stdout)
        self._store_metadata(conn, video_id, info)

        # Not live yet / still live: wait.
        live = info.get("live_status")
        if live == "is_upcoming":
            when = info.get("release_timestamp")
            nxt = db.iso(datetime.fromtimestamp(int(when), UTC)) if when else db.in_iso(hours=1)
            self._requeue(conn, video_id, nxt, "waiting for the scheduled start")
            return Outcome("queued", "upcoming")
        if live in ("is_live", "post_live"):
            self._requeue(conn, video_id, db.in_iso(hours=2), "live stream not finished processing")
            return Outcome("queued", "live")

        # 2. rules (subscription videos only)
        if row["origin"] == "subscription" and row["sub_id"] is not None:
            v = filters.metadata_verdict(row, info)
            if not v.ok:
                with db.tx(conn):
                    conn.execute(
                        "UPDATE videos SET state='filtered', filter_reason=? WHERE id=?", (v.reason, video_id)
                    )
                return Outcome("filtered", v.reason)

        # 3. admission
        est = estimate_bytes(self.cfg, info, max_height)
        if est is None:
            est = int(self.cfg.pool.bitrate_ceiling.get("1080", 8_000_000) * 3600 / 8)
        need = int(est * self.cfg.pool.reservation_factor)
        conn.execute("UPDATE videos SET est_bytes=? WHERE id=?", (est, video_id))
        adm = self.pool.admit(conn, video_id, need)
        if not adm.ok:
            with db.tx(conn):
                conn.execute(
                    "UPDATE videos SET state='deferred_space', last_error_class='space', last_error=?"
                    " WHERE id=?",
                    (adm.reason, video_id),
                )
                db.add_event(
                    conn,
                    "warn",
                    "deferred_space",
                    f"Not enough space for {video_id}: {adm.reason}",
                    {"video_id": video_id, "need": need},
                )
            return Outcome("deferred_space", adm.reason)

        # 4. download
        out = self._download(conn, video_id, info_path, work, max_height, need, cancel)
        if out is not None:
            return out

        # 5. verify + move
        media = next((p for p in work.glob("video.*") if p.suffix in (".mp4", ".m4v")), None)
        if media is None:
            raise Abort(C.Classification(C.UNKNOWN, "no_output", "yt-dlp produced no video.mp4"))
        try:
            meta = verify(media, info.get("duration"))
        except (ValueError, subprocess.TimeoutExpired) as e:
            raise Abort(C.Classification(C.TRANSIENT, "verify_failed", str(e))) from None
        files = {"video.mp4": media}
        thumb = work / "thumb.jpg"
        if thumb.exists():
            files["thumb.jpg"] = thumb
        size = media.stat().st_size
        self.pool.finalize(video_id, files)
        rel = media_rel(video_id)
        warn = meta["vcodec"] != "h264" or meta["acodec"] != "aac"
        with db.tx(conn):
            conn.execute(
                "UPDATE videos SET state='cached', size_bytes=?, file_path=?, thumb_path=COALESCE(?, thumb_path),"
                " height=?, width=?, vcodec=?, acodec=?, downloaded_at=?, evicted_at=NULL, reserved_bytes=NULL,"
                " last_error_class=NULL, last_error=NULL, next_attempt_at=NULL, systemic_failures=0,"
                " subs_state='pending', subs_attempts=0, subs_next_at=NULL WHERE id=?",
                (
                    size,
                    f"{rel}/video.mp4",
                    f"{rel}/thumb.jpg"
                    if "thumb.jpg" in files or (self.cfg.pool_dir / rel / "thumb.jpg").exists()
                    else None,
                    meta["height"],
                    meta["width"],
                    meta["vcodec"],
                    meta["acodec"],
                    db.now_iso(),
                    video_id,
                ),
            )
            db.add_event(
                conn,
                "warn" if warn else "info",
                "cached",
                f"Cached “{info.get('title', video_id)}” ({size / 1e6:.0f} MB, {meta['height']}p"
                + (f", {meta['vcodec']}/{meta['acodec']} — not H.264/AAC" if warn else "")
                + ")",
                {"video_id": video_id, "bytes": size},
            )
        self.health.record(conn, C.Classification(C.OK, "ok"), "download")

        # 6. subtitles (separate, non-fatal)
        if self.cfg.subtitles.langs and not cancel.is_set():
            self._set_progress(conn, video_id, {"stage": "subtitles"})
            subtitles.fetch(self.cfg, self.runner, self.pool, conn, video_id, info_path)
        return Outcome("cached")

    def _download(
        self,
        conn: sqlite3.Connection,
        video_id: str,
        info_path: Path,
        work: Path,
        max_height: int,
        reserved: int,
        cancel: threading.Event,
    ) -> Outcome | None:
        state = {"completed": 0, "last_dl": 0, "last_write": 0.0, "last_check": 0.0, "reserved": reserved}
        overflow = threading.Event()
        local_cancel = threading.Event()

        def watch_cancel() -> None:
            while not local_cancel.is_set():
                if cancel.wait(0.5):
                    local_cancel.set()

        threading.Thread(target=watch_cancel, daemon=True).start()

        def on_line(line: str) -> None:
            p: Progress | None = parse_progress(line)
            if p is None:
                return
            if p.downloaded < state["last_dl"]:
                state["completed"] += state["last_dl"]  # next file of a merged download
            state["last_dl"] = p.downloaded
            now = time.monotonic()
            if now - state["last_check"] > 5:
                state["last_check"] = now
                if self.pool.partial_size(video_id) > state["reserved"]:
                    grow = int(state["reserved"] * 1.25)
                    if self.pool.admit(conn, video_id, grow).ok:
                        state["reserved"] = grow
                    else:
                        overflow.set()
                        local_cancel.set()
            if now - state["last_write"] >= 1:
                state["last_write"] = now
                self._set_progress(
                    conn,
                    video_id,
                    {
                        "stage": "download",
                        "downloaded": state["completed"] + p.downloaded,
                        "total": (state["completed"] + p.total) if p.total else None,
                        "speed": p.speed,
                        "eta": p.eta,
                    },
                )

        try:
            res = self.runner.run(
                self.runner.download_argv(info_path, work, max_height),
                timeout=self.cfg.ytdlp.download_timeout_s,
                on_line=on_line,
                cancel=local_cancel,
            )
        finally:
            local_cancel.set()
        if overflow.is_set():
            with db.tx(conn):
                conn.execute(
                    "UPDATE videos SET state='deferred_space', last_error_class='space',"
                    " last_error='download outgrew its reservation and no space could be freed' WHERE id=?",
                    (video_id,),
                )
            return Outcome("deferred_space", "outgrew reservation")
        if res.cancelled:
            raise Abort(C.Classification(C.TRANSIENT, "cancelled", "cancelled"))
        k = C.classify(res)
        if k.cls != C.OK:
            raise Abort(k)
        return None

    # ---- bookkeeping -----------------------------------------------------------

    def _store_metadata(self, conn: sqlite3.Connection, video_id: str, info: dict[str, Any]) -> None:
        chapters = [
            {"start": c.get("start_time"), "end": c.get("end_time"), "title": c.get("title")}
            for c in info.get("chapters") or []
        ]
        with db.tx(conn):
            upsert_channel(conn, info.get("channel_id"), info)
            conn.execute(
                "UPDATE videos SET channel_id=COALESCE(?, channel_id), title=?, description=?, duration_s=?,"
                " published_at=COALESCE(?, published_at), chapters_json=?, live_status=?, is_short=?,"
                " thumb_url=COALESCE(?, thumb_url) WHERE id=?",
                (
                    info.get("channel_id"),
                    info.get("title"),
                    info.get("description"),
                    int(info["duration"]) if info.get("duration") else None,
                    published_iso(info),
                    json.dumps(chapters) if chapters else None,
                    info.get("live_status"),
                    1 if filters.is_short(info) else 0,
                    info.get("thumbnail"),
                    video_id,
                ),
            )

    def _requeue(self, conn: sqlite3.Connection, video_id: str, next_at: str, why: str) -> None:
        conn.execute(
            "UPDATE videos SET state='queued', next_attempt_at=?, last_error=? WHERE id=?",
            (next_at, why, video_id),
        )

    def _fail(self, conn: sqlite3.Connection, video_id: str, k: C.Classification) -> Outcome:
        if k.reason == "cancelled":
            row = conn.execute("SELECT state FROM videos WHERE id=?", (video_id,)).fetchone()
            if row and row["state"] == "downloading":
                conn.execute("UPDATE videos SET state='queued' WHERE id=?", (video_id,))
            return Outcome("queued", "cancelled")

        self.health.record(conn, k, "download")
        row = conn.execute(
            "SELECT attempts, systemic_failures, title FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        attempts, systemic = (row["attempts"], row["systemic_failures"]) if row else (0, 0)
        err = (k.stderr if k.cls == C.UNKNOWN and k.stderr else k.message)[-4000:]
        if k.cls == C.VIDEO:
            if k.reason == "upcoming":
                self._requeue(conn, video_id, db.in_iso(hours=1), k.message)
                return Outcome("queued", "upcoming")
            with db.tx(conn):
                conn.execute(
                    "UPDATE videos SET state='unavailable', last_error_class=?, last_error=? WHERE id=?",
                    (k.cls, f"{k.reason}: {err}", video_id),
                )
                db.add_event(
                    conn,
                    "info",
                    "unavailable",
                    f"{video_id} is unavailable ({k.reason})",
                    {"video_id": video_id, "message": k.message[:300]},
                )
            return Outcome("unavailable", k.reason)

        if k.cls == C.SYSTEMIC:
            systemic += 1
            # Doesn't count against attempts, but a video that keeps failing "systemically" while
            # everything else works is really a broken video.
            if systemic >= self.cfg.ytdlp.max_attempts * 2 and self.health.get(conn)["state"] == "ok":
                state, nxt = "failed", None
            else:
                state, nxt = "queued", db.in_iso(minutes=30)
            conn.execute(
                "UPDATE videos SET state=?, systemic_failures=?, last_error_class=?, last_error=?,"
                " next_attempt_at=? WHERE id=?",
                (state, systemic, k.cls, f"{k.reason}: {err}", nxt, video_id),
            )
            return Outcome(state, k.reason)

        attempts += 1
        if attempts >= self.cfg.ytdlp.max_attempts:
            with db.tx(conn):
                conn.execute(
                    "UPDATE videos SET state='failed', attempts=?, last_error_class=?, last_error=?,"
                    " next_attempt_at=NULL WHERE id=?",
                    (attempts, k.cls, f"{k.reason}: {err}", video_id),
                )
                db.add_event(
                    conn,
                    "error",
                    "failed",
                    f"Giving up on {video_id} after {attempts} attempts",
                    {"video_id": video_id, "reason": k.reason, "stderr": (k.stderr or k.message)[-4000:]},
                )
            return Outcome("failed", k.reason)
        backoff_min = min(5 * 2 ** (attempts - 1), 12 * 60)
        conn.execute(
            "UPDATE videos SET state='queued', attempts=?, last_error_class=?, last_error=?, next_attempt_at=?"
            " WHERE id=?",
            (attempts, k.cls, f"{k.reason}: {err}", db.in_iso(minutes=backoff_min), video_id),
        )
        if k.cls == C.UNKNOWN:
            db.add_event(
                conn,
                "warn",
                "unknown_error",
                f"Unrecognised yt-dlp failure for {video_id}",
                {"video_id": video_id, "stderr": (k.stderr or k.message)[-4000:]},
            )
        return Outcome("queued", k.reason)
