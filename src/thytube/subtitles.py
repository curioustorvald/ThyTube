"""Subtitle selection, fetching and VTT cleaning.

YouTube auto-captions arrive as "rolling" cues: each cue repeats the previous line and types the
new one with inline ``<00:00:01.234><c> word</c>`` timing tags, separated by ~10 ms transition
cues. ``clean_vtt`` turns that into plain, non-overlapping cues; manual subtitles are only
normalised.
"""

from __future__ import annotations

import html
import json
import logging
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db
from .config import Config
from .pool import Pool, media_rel
from .ytdlp import classify as C
from .ytdlp.runner import Runner

log = logging.getLogger(__name__)

_TS = r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"
_CUE_TIME = re.compile(rf"^\s*{_TS}\s+-->\s+{_TS}")
_TAG = re.compile(r"<[^>]*>")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _secs(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _fmt(t: float) -> str:
    t = max(t, 0.0)
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_vtt(text: str) -> list[tuple[float, float, list[str]]]:
    cues: list[tuple[float, float, list[str]]] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _CUE_TIME.match(lines[i])
        if not m:
            i += 1
            continue
        g = m.groups()
        start, end = _secs(*g[0:4]), _secs(*g[4:8])
        i += 1
        body: list[str] = []
        # YouTube puts single-space lines inside cues, so only a truly empty line ends one.
        while i < len(lines) and lines[i] != "" and not _CUE_TIME.match(lines[i]):
            body.append(lines[i])
            i += 1
        cues.append((start, end, body))
    return cues


def _clean_line(line: str) -> str:
    return html.unescape(_TAG.sub("", line)).replace("​", "").strip()


def clean_vtt(text: str, kind: str = "auto") -> str:
    raw = parse_vtt(text)
    out: list[Cue] = []
    if kind == "auto":
        last_line = ""
        for start, end, body in raw:
            lines = [ln for ln in (_clean_line(b) for b in body) if ln]
            if end - start < 0.05:
                # Rolling transition cue: it shows the finished line, so the previous cue lasts until here.
                if out and lines and lines[-1] == last_line:
                    out[-1].end = max(out[-1].end, end)
                continue
            # Drop the leading line(s) that repeat what was already shown.
            while lines and lines[0] == last_line:
                lines.pop(0)
            if not lines:
                if out:
                    out[-1].end = max(out[-1].end, end)
                continue
            txt = "\n".join(lines)
            last_line = lines[-1]
            if out and out[-1].text == txt:
                out[-1].end = max(out[-1].end, end)
                continue
            if out and out[-1].end > start:
                out[-1].end = start
            out.append(Cue(start, end, txt))
    else:
        for start, end, body in raw:
            lines = [ln for ln in (_clean_line(b) for b in body) if ln]
            if lines and end > start:
                out.append(Cue(start, end, "\n".join(lines)))
    out = [c for c in out if c.end - c.start >= 0.05]
    parts = ["WEBVTT", ""]
    for c in out:
        esc = c.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts += [f"{_fmt(c.start)} --> {_fmt(c.end)}", esc, ""]
    return "\n".join(parts)


# ---- language selection ------------------------------------------------------


def _base(code: str) -> str:
    return code.split("-")[0].lower()


def choose_tracks(info: dict[str, Any], cfg: Config) -> list[tuple[str, str, str]]:
    """[(yt-dlp sub code, our lang, kind)] for the configured languages."""
    wanted = [lang.lower() for lang in cfg.subtitles.langs]
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    autos = info.get("automatic_captions") or {}
    chosen: list[tuple[str, str, str]] = []
    have: set[str] = set()
    for lang in wanted:
        for code in sorted(manual, key=lambda c: (c != lang, c)):
            if _base(code) == _base(lang):
                chosen.append((code, lang, "manual"))
                have.add(lang)
                break
    if cfg.subtitles.auto:
        originals = [k[: -len("-orig")] for k in autos if k.endswith("-orig")]
        if not originals and info.get("language") and info["language"] in autos:
            originals = [info["language"]]
        for orig in originals:
            lang = next((w for w in wanted if _base(w) == _base(orig)), None)
            if lang and lang not in have:
                code = f"{orig}-orig" if f"{orig}-orig" in autos else orig
                chosen.append((code, lang, "auto"))
                have.add(lang)
        if cfg.subtitles.auto_translated:
            for lang in wanted:
                if lang not in have and lang in autos:
                    chosen.append((lang, lang, "auto"))
                    have.add(lang)
    return chosen


# ---- fetching ------------------------------------------------------------------


def fetch(
    cfg: Config,
    runner: Runner,
    pool: Pool,
    conn: sqlite3.Connection,
    video_id: str,
    info_path: Path | None = None,
) -> str:
    """Fetch, clean and store subtitles for a cached video. Never raises for yt-dlp failures;
    returns the new subs_state (done | pending | failed)."""
    work = cfg.partial_dir / f"{video_id}.subs"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        if info_path is None or not info_path.exists():
            res = runner.run(runner.metadata_argv(video_id), timeout=cfg.ytdlp.metadata_timeout_s)
            k = C.classify(res)
            if k.cls != C.OK:
                return _subs_failed(cfg, conn, video_id, k)
            info_path = work / "info.json"
            info_path.write_text(res.stdout)
        info = json.loads(info_path.read_text())
        tracks = choose_tracks(info, cfg)
        if not tracks:
            _subs_done(conn, video_id)
            return "done"
        res = runner.run(
            runner.subtitles_argv(info_path, work, [t[0] for t in tracks]),
            timeout=cfg.ytdlp.metadata_timeout_s,
        )
        stored = 0
        target_dir = cfg.pool_dir / media_rel(video_id) / "subs"
        for code, lang, kind in tracks:
            src = work / f"sub.{code}.vtt"
            if not src.exists():
                continue
            cleaned = clean_vtt(src.read_text(errors="replace"), kind)
            with pool.lock:
                if not (cfg.pool_dir / media_rel(video_id)).exists():
                    break  # evicted meanwhile
                target_dir.mkdir(parents=True, exist_ok=True)
                tmp = target_dir / f".{lang}.{kind}.vtt.tmp"
                tmp.write_text(cleaned)
                tmp.replace(target_dir / f"{lang}.{kind}.vtt")
            rel = f"{media_rel(video_id)}/subs/{lang}.{kind}.vtt"
            conn.execute(
                "INSERT INTO subtitles(video_id, lang, kind, path) VALUES(?,?,?,?) "
                "ON CONFLICT(video_id, lang, kind) DO UPDATE SET path=excluded.path",
                (video_id, lang, kind, rel),
            )
            stored += 1
        k = C.classify(res)
        if k.cls != C.OK and stored < len(tracks):
            return _subs_failed(cfg, conn, video_id, k)
        _subs_done(conn, video_id)
        return "done"
    except Exception as e:  # noqa: BLE001 - subtitles are never fatal
        log.exception("subtitle fetch failed for %s", video_id)
        return _subs_failed(cfg, conn, video_id, C.Classification(C.UNKNOWN, "exception", str(e)))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _subs_done(conn: sqlite3.Connection, video_id: str) -> None:
    conn.execute("UPDATE videos SET subs_state='done', subs_next_at=NULL WHERE id=?", (video_id,))


def _subs_failed(cfg: Config, conn: sqlite3.Connection, video_id: str, k: C.Classification) -> str:
    row = conn.execute("SELECT subs_attempts FROM videos WHERE id=?", (video_id,)).fetchone()
    attempts = (row["subs_attempts"] if row else 0) + 1
    if k.cls == C.VIDEO or attempts >= cfg.subtitles.max_attempts:
        state, nxt = "failed", None
    else:
        state, nxt = "pending", db.in_iso(minutes=cfg.subtitles.retry_minutes * attempts)
    conn.execute(
        "UPDATE videos SET subs_state=?, subs_attempts=?, subs_next_at=? WHERE id=?",
        (state, attempts, nxt, video_id),
    )
    db.add_event(
        conn,
        "info" if state == "pending" else "warn",
        "subtitles",
        f"Subtitles for {video_id} failed ({k.reason}); {'will retry' if state == 'pending' else 'giving up'}",
        {"video_id": video_id, "message": k.message[:300]},
    )
    return state
