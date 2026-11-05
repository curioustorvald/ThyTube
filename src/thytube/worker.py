"""The worker: the only process that talks to YouTube in bulk and the only writer to the pool.

Main loop (every ~2 s):
  1. commands from the web process (via the ``commands`` table)
  2. health: canary probe when blocked and due; yt-dlp update when requested
  3. due subscription polls and subtitle retries (one background task at a time)
  4. start downloads while slots are free
  5. nightly reconcile + pruning; a heartbeat in kv
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import sqlite3
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any

from . import db, discovery, playback, subtitles
from .config import Config
from .downloader import PROGRESS_KEY, Downloader
from .health import Health, run_canary
from .pool import Pool
from .ytdlp import updater
from .ytdlp.runner import Runner

log = logging.getLogger("thytube.worker")

TICK_S = 2.0


class Worker:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.runner = Runner(cfg)
        self.pool = Pool(cfg)
        self.health = Health(cfg)
        self.downloader = Downloader(cfg, self.runner, self.pool, self.health)
        self.discovery = discovery.Discovery(cfg, self.runner, self.pool, self.health)
        self.stop = threading.Event()
        self.dl_pool = ThreadPoolExecutor(max_workers=cfg.ytdlp.concurrency, thread_name_prefix="dl")
        self.bg_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bg")
        self.active: dict[str, tuple[Future[Any], threading.Event]] = {}
        self.bg: Future[Any] | None = None
        self.bg_name = ""
        self._local = threading.local()
        self.conn: sqlite3.Connection | None = None

    # ---- connections (one per thread) -------------------------------------

    def tconn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = db.connect(self.cfg.db_path)
            self._local.conn = c
        return c

    # ---- lifecycle ----------------------------------------------------------

    def run(self) -> int:
        self.cfg.ensure_dirs()
        self.cfg.ensure_pool_dirs()
        lock = open(self.cfg.run_dir / "worker.lock", "w")  # noqa: SIM115
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("another worker is already running")
            return 1
        lock.write(str(os.getpid()))
        lock.flush()
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        self.conn = db.open_db(self.cfg.db_path)
        self.startup()
        log.info("worker started")
        try:
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - the loop must survive anything
                    log.exception("worker tick failed")
                self.stop.wait(TICK_S)
        finally:
            self.shutdown()
            lock.close()
        return 0

    def startup(self) -> None:
        conn = self.conn
        assert conn is not None
        with db.tx(conn):
            n = conn.execute("UPDATE videos SET state='queued' WHERE state='downloading'").rowcount
            conn.execute("UPDATE videos SET reserved_bytes=NULL WHERE reserved_bytes IS NOT NULL")
            db.kv_delete(conn, PROGRESS_KEY)
            # A crashed update leaves this behind.
            db.kv_delete(conn, "ytdlp_updating")
            db.add_event(conn, "info", "worker", "Worker started", {"requeued": n})
        self.pool.reconcile(conn)
        version = updater.refresh_version(self.cfg)
        info = db.kv_get(conn, "ytdlp") or {}
        if version and info.get("version") != version:
            info["version"] = version
            db.kv_set(conn, "ytdlp", info)
        if version is None:
            db.add_event(
                conn, "warn", "ytdlp", f"yt-dlp binary not found or not runnable: {self.cfg.ytdlp_binary}"
            )

    def shutdown(self) -> None:
        log.info("worker stopping: cancelling %d download(s)", len(self.active))
        for _fut, cancel in self.active.values():
            cancel.set()
        self.dl_pool.shutdown(wait=True, cancel_futures=True)
        self.bg_pool.shutdown(wait=True, cancel_futures=True)
        if self.conn is not None:
            with db.tx(self.conn):
                self.conn.execute("UPDATE videos SET state='queued' WHERE state='downloading'")
                self.conn.execute("UPDATE videos SET reserved_bytes=NULL WHERE reserved_bytes IS NOT NULL")
                db.kv_delete(self.conn, PROGRESS_KEY)
                db.add_event(self.conn, "info", "worker", "Worker stopped")
            self.conn.close()

    # ---- the loop -------------------------------------------------------------

    def tick(self) -> None:
        conn = self.conn
        assert conn is not None
        self._reap()
        self.handle_commands()
        allowed = self.health.allows_youtube(conn)
        if self.bg_idle():
            if self.health.probe_due(conn):
                self.submit_bg("probe", self._probe)
            elif allowed:
                self._schedule_poll_or_subs()
        if allowed:
            self._start_downloads()
        self._housekeeping()
        db.kv_set(
            conn, "worker_heartbeat", {"at": db.now_iso(), "active": sorted(self.active), "bg": self.bg_name}
        )

    def _reap(self) -> None:
        for vid, (fut, _c) in list(self.active.items()):
            if fut.done():
                del self.active[vid]
                exc = fut.exception()
                if exc:
                    log.error("download thread for %s raised: %r", vid, exc)
        if self.bg is not None and self.bg.done():
            exc = self.bg.exception()
            if exc:
                log.error("background task %s raised: %r", self.bg_name, exc)
            self.bg, self.bg_name = None, ""

    def bg_idle(self) -> bool:
        return self.bg is None

    def submit_bg(self, name: str, fn: Any, *args: Any) -> None:
        log.info("background: %s", name)
        self.bg_name = name
        self.bg = self.bg_pool.submit(fn, *args)

    # ---- commands ---------------------------------------------------------------

    def handle_commands(self) -> None:
        conn = self.conn
        assert conn is not None
        for cmd in db.take_commands(conn):
            payload = json.loads(cmd["payload_json"] or "{}")
            try:
                result = self._command(cmd["kind"], payload)
            except Exception as e:  # noqa: BLE001
                log.exception("command %s failed", cmd["kind"])
                result = {"ok": False, "error": str(e)}
            if result is not None:
                db.finish_command(conn, cmd["id"], result)
            else:
                # Deferred (background busy): put it back.
                conn.execute("UPDATE commands SET taken_at=NULL WHERE id=?", (cmd["id"],))

    def _command(self, kind: str, p: dict[str, Any]) -> dict[str, Any] | None:
        conn = self.conn
        assert conn is not None
        if kind == "delete_files":
            done = []
            for vid in p.get("video_ids", []):
                if vid in self.active:
                    self.active[vid][1].set()
                    self.active[vid][0].result(timeout=60)
                row = conn.execute("SELECT state FROM videos WHERE id=?", (vid,)).fetchone()
                if row and row["state"] in ("cached",):
                    self.pool.evict(conn, vid, p.get("reason", "deleted by user"))
                    done.append(vid)
                elif row and row["state"] in ("queued", "downloading", "deferred_space"):
                    conn.execute(
                        "UPDATE videos SET state='filtered', filter_reason=? WHERE id=?",
                        (p.get("reason", "deleted by user"), vid),
                    )
            return {"ok": True, "deleted": done}
        if kind == "cancel_download":
            vid = p.get("video_id")
            if vid in self.active:
                self.active[vid][1].set()
                self.active[vid][0].result(timeout=60)
            conn.execute(
                "UPDATE videos SET state='filtered', filter_reason='cancelled' WHERE id=? AND state IN"
                " ('queued','downloading','deferred_space')",
                (vid,),
            )
            return {"ok": True}
        if kind == "reconcile":
            return {"ok": True, "report": self.pool.reconcile(conn, active=set(self.active))}
        # The rest need the single background slot.
        if not self.bg_idle():
            return None
        if kind == "poll_subscription":
            self.submit_bg(f"poll {p.get('id')}", self._poll, int(p["id"]), bool(p.get("backfill")))
        elif kind == "probe":
            self.submit_bg("probe", self._probe)
        elif kind == "update_ytdlp":
            self.submit_bg("update yt-dlp", self._update)
        elif kind == "rollback_ytdlp":
            self.submit_bg("rollback yt-dlp", self._rollback)
        else:
            return {"ok": False, "error": f"unknown command {kind}"}
        return {"ok": True, "started": True}

    # ---- background tasks -------------------------------------------------------

    def _poll(self, sub_id: int, backfill: bool = False) -> None:
        self.discovery.poll(self.tconn(), sub_id, force_backfill=backfill)

    def _probe(self) -> None:
        conn = self.tconn()
        result = run_canary(self.cfg, self.runner)
        self.health.on_probe(conn, result)

    def _update(self) -> None:
        result = updater.update(self.cfg)
        log.info("yt-dlp update: %s", result.get("message"))

    def _rollback(self) -> None:
        updater.rollback(self.cfg)

    def _subs(self, video_id: str) -> None:
        subtitles.fetch(self.cfg, self.runner, self.pool, self.tconn(), video_id)

    def _schedule_poll_or_subs(self) -> None:
        conn = self.conn
        assert conn is not None
        now = db.now_iso()
        sub = conn.execute(
            "SELECT id, backfill_done FROM subscriptions WHERE enabled=1 AND (next_poll_at IS NULL OR next_poll_at <= ?)"
            " ORDER BY next_poll_at IS NOT NULL, next_poll_at LIMIT 1",
            (now,),
        ).fetchone()
        if sub is not None:
            self.submit_bg(f"poll {sub['id']}", self._poll, sub["id"], False)
            return
        placeholders = ",".join("?" * len(self.active)) or "''"
        row = conn.execute(
            "SELECT id FROM videos WHERE state='cached' AND subs_state='pending'"
            f" AND (subs_next_at IS NULL OR subs_next_at <= ?) AND id NOT IN ({placeholders}) LIMIT 1",
            (now, *self.active),
        ).fetchone()
        if row is not None:
            self.submit_bg(f"subtitles {row['id']}", self._subs, row["id"])

    # ---- downloads ----------------------------------------------------------------

    def _start_downloads(self) -> None:
        conn = self.conn
        assert conn is not None
        while len(self.active) < self.cfg.ytdlp.concurrency and not self.stop.is_set():
            with db.tx(conn):
                row = conn.execute(
                    "SELECT id FROM videos WHERE state='queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
                    " ORDER BY origin='manual' DESC, queued_at ASC, discovered_at ASC LIMIT 1",
                    (db.now_iso(),),
                ).fetchone()
                if row is None:
                    return
                conn.execute("UPDATE videos SET state='downloading' WHERE id=?", (row["id"],))
            vid = row["id"]
            cancel = threading.Event()
            fut = self.dl_pool.submit(self._download, vid, cancel)
            self.active[vid] = (fut, cancel)

    def _download(self, vid: str, cancel: threading.Event) -> None:
        stop_or_cancel = threading.Event()

        def link() -> None:
            while not stop_or_cancel.is_set():
                if self.stop.wait(0.5) or cancel.is_set():
                    stop_or_cancel.set()

        t = threading.Thread(target=link, daemon=True)
        t.start()
        try:
            outcome = self.downloader.process(self.tconn(), vid, stop_or_cancel)
            log.info("%s → %s %s", vid, outcome.state, outcome.detail)
        finally:
            stop_or_cancel.set()

    # ---- housekeeping ---------------------------------------------------------------

    def _housekeeping(self) -> None:
        conn = self.conn
        assert conn is not None
        last = db.parse_iso((db.kv_get(conn, "last_reconcile") or {}).get("at"))
        due = last is None or (db.now() - last).total_seconds() > 23 * 3600
        if due and datetime.now().hour == 3 and not self.active:
            self.pool.reconcile(conn)
            db.prune_events(conn)
            db.prune_commands(conn)
            playback.prune_sessions(conn)
        # Deferred-for-space items get another chance once room appears (deletes, budget changes),
        # judged from the stored estimate so this costs no YouTube requests.
        last_retry = db.kv_get(conn, "deferred_retry_at")
        if last_retry is None or time.time() - last_retry > 900:
            db.kv_set(conn, "deferred_retry_at", time.time())
            self._retry_deferred(conn)

    def _retry_deferred(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT id, est_bytes FROM videos WHERE state='deferred_space' ORDER BY queued_at LIMIT 50"
        ).fetchall()
        if not rows:
            return
        usage = self.pool.usage(conn)
        evictable = sum(r["size_bytes"] or 0 for r in self.pool.eviction_candidates(conn))
        room = min(
            self.cfg.pool.max_bytes - usage["cached_bytes"] - usage["reserved_bytes"] + evictable,
            (usage["fs_free"] or 0) - self.cfg.pool.min_free_bytes + evictable,
        )
        for r in rows:
            need = int((r["est_bytes"] or 0) * self.cfg.pool.reservation_factor)
            if need <= room:
                conn.execute("UPDATE videos SET state='queued', next_attempt_at=NULL WHERE id=?", (r["id"],))
                room -= need
