"""Health state machine — the "YouTube is being hostile" circuit breaker.

    ok ──(≥ degraded_after systemic in a row)──► degraded ──(≥ blocked_after)──► blocked
    any success ──► ok
    blocked: no downloads/discovery; a canary probe runs with exponential backoff
    updating: a yt-dlp update is running (flag set by the updater)

State lives in ``kv['health']`` and is changed inside a write transaction so the web process
(search/resolve outcomes) and the worker can both report into it.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from typing import Any

from . import db
from .config import Config
from .ytdlp import classify as C
from .ytdlp.runner import Runner

OK, DEGRADED, BLOCKED, UPDATING = "ok", "degraded", "blocked", "updating"
UPDATING_STALE_S = 3600


def _default() -> dict[str, Any]:
    return {
        "state": OK,
        "since": db.now_iso(),
        "consecutive_systemic": 0,
        "consecutive_rate_limited": 0,
        "last_success_at": None,
        "last_failure": None,
        "next_probe_at": None,
        "probe_backoff_minutes": None,
        "last_probe": None,
    }


class Health:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def get(self, conn: sqlite3.Connection) -> dict[str, Any]:
        h = {**_default(), **(db.kv_get(conn, "health") or {})}
        upd = db.kv_get(conn, "ytdlp_updating")
        if upd and time.time() - upd.get("started", 0) < UPDATING_STALE_S:
            h["effective_state"] = UPDATING
        else:
            h["effective_state"] = h["state"]
        return h

    def allows_youtube(self, conn: sqlite3.Connection) -> bool:
        return self.get(conn)["effective_state"] in (OK, DEGRADED)

    def _save(self, conn: sqlite3.Connection, h: dict[str, Any]) -> None:
        h.pop("effective_state", None)
        db.kv_set(conn, "health", h)

    def _transition(self, conn: sqlite3.Connection, h: dict[str, Any], new: str, why: str) -> None:
        old = h["state"]
        if old == new:
            return
        h["state"] = new
        h["since"] = db.now_iso()
        level = {OK: "info", DEGRADED: "warn", BLOCKED: "error"}.get(new, "info")
        db.add_event(conn, level, "health", f"Health {old} → {new}: {why}", {"from": old, "to": new})

    def record(self, conn: sqlite3.Connection, k: C.Classification, source: str) -> str:
        """Feed one yt-dlp outcome into the state machine. Returns the new state."""
        with db.tx(conn):
            h = self.get(conn)
            if k.cls == C.OK:
                h["consecutive_systemic"] = 0
                h["consecutive_rate_limited"] = 0
                h["last_success_at"] = db.now_iso()
                if h["state"] != OK:
                    self._transition(conn, h, OK, f"{source} succeeded")
                    h["next_probe_at"] = None
                    h["probe_backoff_minutes"] = None
                    _release_held(conn)
                self._save(conn, h)
                return h["state"]

            systemic = k.cls == C.SYSTEMIC
            if k.cls == C.TRANSIENT and k.reason == "rate_limited":
                h["consecutive_rate_limited"] += 1
                systemic = h["consecutive_rate_limited"] >= 2  # repeated 429s are a block
            elif k.cls != C.VIDEO:
                h["consecutive_rate_limited"] = 0
            if k.cls != C.VIDEO:
                h["last_failure"] = {
                    "at": db.now_iso(),
                    "cls": k.cls,
                    "reason": k.reason,
                    "message": k.message[:500],
                    "source": source,
                }
            if systemic:
                h["consecutive_systemic"] += 1
                n = h["consecutive_systemic"]
                if n >= self.cfg.health.blocked_after and h["state"] != BLOCKED:
                    self._transition(conn, h, BLOCKED, f"{n} systemic failures in a row ({k.reason})")
                    self._schedule_probe(h, first=True)
                    self._maybe_request_update(conn)
                elif n >= self.cfg.health.degraded_after and h["state"] == OK:
                    self._transition(conn, h, DEGRADED, f"{n} systemic failures in a row ({k.reason})")
            self._save(conn, h)
            return h["state"]

    # ---- probing ----------------------------------------------------------

    def _schedule_probe(self, h: dict[str, Any], first: bool = False) -> None:
        lo = self.cfg.health.probe_min_backoff_minutes
        hi = self.cfg.health.probe_max_backoff_hours * 60
        prev = h.get("probe_backoff_minutes")
        minutes = lo if first or not prev else min(prev * 2, hi)
        h["probe_backoff_minutes"] = minutes
        h["next_probe_at"] = db.in_iso(minutes=minutes)

    def _maybe_request_update(self, conn: sqlite3.Connection) -> None:
        info = db.kv_get(conn, "ytdlp") or {}
        last = db.parse_iso((info.get("last_update") or {}).get("at"))
        threshold = self.cfg.health.update_on_block_after_hours * 3600
        if last is None or (db.now() - last).total_seconds() > threshold:
            db.enqueue_command(conn, "update_ytdlp", {"reason": "blocked"})

    def probe_due(self, conn: sqlite3.Connection) -> bool:
        h = self.get(conn)
        if h["state"] != BLOCKED or h["effective_state"] == UPDATING:
            return False
        nxt = db.parse_iso(h.get("next_probe_at"))
        return nxt is None or nxt <= db.now()

    def on_probe(self, conn: sqlite3.Connection, result: dict[str, Any]) -> None:
        with db.tx(conn):
            h = self.get(conn)
            h["last_probe"] = {
                "at": db.now_iso(),
                "ok": result["ok"],
                "stage": result.get("stage"),
                "message": result.get("message", "")[:500],
            }
            if result["ok"]:
                db.add_event(conn, "info", "probe", "Canary probe passed", {"elapsed": result.get("elapsed")})
                h["consecutive_systemic"] = 0
                h["consecutive_rate_limited"] = 0
                h["last_success_at"] = db.now_iso()
                if h["state"] != OK:
                    self._transition(conn, h, OK, "canary probe passed")
                h["next_probe_at"] = None
                h["probe_backoff_minutes"] = None
                _release_held(conn)
            else:
                db.add_event(
                    conn,
                    "warn",
                    "probe",
                    f"Canary probe failed at {result.get('stage')}: {result.get('message', '')[:200]}",
                    {"reason": result.get("reason")},
                )
                if result.get("cls") == C.SYSTEMIC or h["state"] == BLOCKED:
                    if h["state"] != BLOCKED:
                        self._transition(conn, h, BLOCKED, "canary probe failed")
                        self._schedule_probe(h, first=True)
                    else:
                        self._schedule_probe(h)
            self._save(conn, h)

    def force_probe(self, conn: sqlite3.Connection) -> None:
        with db.tx(conn):
            h = self.get(conn)
            h["next_probe_at"] = db.now_iso()
            self._save(conn, h)


def _release_held(conn: sqlite3.Connection) -> None:
    """Items held back for systemic reasons go straight back into the queue."""
    conn.execute(
        "UPDATE videos SET next_attempt_at=NULL WHERE state='queued' AND last_error_class='systemic'"
    )


def run_canary(cfg: Config, runner: Runner) -> dict[str, Any]:
    """Metadata + a short ranged download of a stable video. Never touches the pool."""
    work = cfg.run_dir / f"canary-{int(time.time() * 1000)}"
    work.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    try:
        res = runner.run(
            runner.metadata_argv(cfg.health.canary_video, 360, interactive=True),
            timeout=cfg.ytdlp.metadata_timeout_s,
        )
        k = C.classify(res)
        if k.cls != C.OK:
            return _canary_result(False, "metadata", k, start)
        info_path = work / "info.json"
        try:
            json.loads(res.stdout)
        except json.JSONDecodeError:
            return _canary_result(
                False, "metadata", C.Classification(C.UNKNOWN, "bad_json", "invalid JSON"), start
            )
        info_path.write_text(res.stdout)
        res = runner.run(runner.canary_download_argv(info_path, work), timeout=180)
        k = C.classify(res)
        if k.cls != C.OK:
            return _canary_result(False, "download", k, start)
        got = [p for p in work.glob("canary.*") if p.stat().st_size > 0]
        if not got:
            k = C.Classification(C.SYSTEMIC, "empty_download", "canary download produced no data")
            return _canary_result(False, "download", k, start)
        return _canary_result(True, "done", C.Classification(C.OK, "ok"), start)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _canary_result(ok: bool, stage: str, k: C.Classification, start: float) -> dict[str, Any]:
    return {
        "ok": ok,
        "stage": stage,
        "cls": k.cls,
        "reason": k.reason,
        "message": k.message,
        "elapsed": round(time.monotonic() - start, 2),
    }


def explain(h: dict[str, Any]) -> str:
    """Plain-language explanation for the Status page."""
    state = h.get("effective_state", h["state"])
    if state == OK:
        return "Downloads and channel checks run normally."
    if state == UPDATING:
        return "Updating yt-dlp. Downloads resume once the update has been checked."
    if state == DEGRADED:
        lf = h.get("last_failure") or {}
        return (
            "Some requests to YouTube are failing "
            f"({lf.get('reason', 'unknown')}). Downloads continue, but may stop if this keeps happening."
        )
    nxt = h.get("next_probe_at")
    when = ""
    if nxt:
        when = f" Next check at {nxt[11:16]} UTC."
    return "YouTube is blocking downloads; waiting for a yt-dlp fix or for the block to lift." + when
