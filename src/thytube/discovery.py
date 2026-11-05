"""Channel discovery: RSS for routine polls, yt-dlp flat listings for backfill and as a fallback."""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from . import db, filters
from .config import Config
from .health import Health
from .pool import Pool, avatar_rel
from .ytdlp import classify as C
from .ytdlp.runner import Runner, channel_url

log = logging.getLogger(__name__)

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ThyTube/0.1"


class DiscoveryError(Exception):
    def __init__(self, k: C.Classification) -> None:
        super().__init__(k.message)
        self.k = k


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall("a:entry", NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=NS)
        if not vid:
            continue
        link = entry.find("a:link", NS)
        href = link.get("href", "") if link is not None else ""
        thumb = entry.find("media:group/media:thumbnail", NS)
        out.append(
            {
                "id": vid,
                "title": entry.findtext("a:title", default="", namespaces=NS),
                "published_at": _norm_ts(entry.findtext("a:published", default="", namespaces=NS)),
                "channel_id": entry.findtext("yt:channelId", default="", namespaces=NS) or None,
                "is_short": "/shorts/" in href,
                "thumb_url": thumb.get("url") if thumb is not None else None,
            }
        )
    return out


def parse_rss_channel(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    return {"title": root.findtext("a:title", default="", namespaces=NS)}


def _norm_ts(s: str) -> str | None:
    dt = db.parse_iso(s) if s else None
    return db.iso(dt) if dt else None


def fetch_rss(cfg: Config, channel_id: str) -> str:
    url = cfg.discovery.rss_url_template.format(channel_id=channel_id)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=cfg.discovery.http_timeout_s) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def flat_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in data.get("entries") or []:
        if not e or not e.get("id") or len(e["id"]) != 11:
            continue
        thumbs = e.get("thumbnails") or []
        url = e.get("url") or ""
        out.append(
            {
                "id": e["id"],
                "title": e.get("title"),
                "duration": e.get("duration"),
                "live_status": e.get("live_status"),
                "is_short": "/shorts/" in url,
                "thumb_url": thumbs[-1]["url"] if thumbs else None,
                "channel_id": e.get("channel_id"),
            }
        )
    return out


def channel_meta_from_listing(data: dict[str, Any]) -> dict[str, Any]:
    thumbs = data.get("thumbnails") or []
    avatar = next((t["url"] for t in thumbs if t.get("id") == "avatar_uncropped"), None)
    if avatar is None and thumbs:
        # Pick a square-ish one.
        sq = [t for t in thumbs if t.get("width") and t.get("width") == t.get("height")]
        avatar = (sq or thumbs)[-1].get("url")
    title = data.get("channel") or data.get("uploader") or data.get("title")
    if title and title.endswith(" - Videos"):
        title = title[: -len(" - Videos")]
    handle = data.get("uploader_id") if str(data.get("uploader_id") or "").startswith("@") else None
    return {
        "id": data.get("channel_id") or data.get("id"),
        "title": title,
        "handle": handle,
        "avatar_url": avatar,
    }


def list_channel(
    runner: Runner, cfg: Config, channel_id: str, tab: str, limit: int, *, interactive: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    res = runner.run(
        runner.flat_listing_argv(channel_url(channel_id, tab), limit, interactive=interactive),
        timeout=cfg.ytdlp.metadata_timeout_s,
    )
    k = C.classify(res)
    if k.cls != C.OK:
        # An absent /shorts or /streams tab is not an error worth reporting.
        if tab != "videos" and k.cls == C.VIDEO:
            return [], {}
        raise DiscoveryError(k)
    data = json.loads(res.stdout)
    entries = flat_entries(data)
    if tab == "shorts":
        for e in entries:
            e["is_short"] = True
    return entries, channel_meta_from_listing(data)


def upsert_channel_meta(conn: sqlite3.Connection, meta: dict[str, Any]) -> None:
    if not meta.get("id"):
        return
    conn.execute(
        "INSERT INTO channels(id, title, handle, avatar_url, updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET title=COALESCE(excluded.title, title),"
        " handle=COALESCE(excluded.handle, handle), avatar_url=COALESCE(excluded.avatar_url, avatar_url),"
        " updated_at=excluded.updated_at",
        (meta["id"], meta.get("title"), meta.get("handle"), meta.get("avatar_url"), db.now_iso()),
    )


def fetch_avatar(cfg: Config, pool: Pool, conn: sqlite3.Connection, channel_id: str) -> None:
    row = conn.execute("SELECT avatar_url, avatar_path FROM channels WHERE id=?", (channel_id,)).fetchone()
    if not row or not row["avatar_url"] or row["avatar_path"]:
        return
    rel = avatar_rel(channel_id)
    target = cfg.pool_dir / rel
    try:
        req = urllib.request.Request(row["avatar_url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=cfg.discovery.http_timeout_s) as resp:  # noqa: S310
            data = resp.read(5_000_000)
        with pool.lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
        conn.execute("UPDATE channels SET avatar_path=? WHERE id=?", (rel, channel_id))
    except Exception as e:  # noqa: BLE001 - avatars are cosmetic
        log.info("avatar fetch for %s failed: %s", channel_id, e)


class Discovery:
    def __init__(self, cfg: Config, runner: Runner, pool: Pool, health: Health) -> None:
        self.cfg = cfg
        self.runner = runner
        self.pool = pool
        self.health = health

    def next_poll_at(self) -> str:
        d = self.cfg.discovery
        jitter = random.uniform(-d.jitter_minutes, d.jitter_minutes)
        return db.in_iso(minutes=max(d.interval_minutes + jitter, 5))

    def _tabs(self, subs: list[sqlite3.Row]) -> list[str]:
        tabs = ["videos"]
        if any(s["include_shorts"] for s in subs):
            tabs.append("shorts")
        if any(s["include_streams"] for s in subs):
            tabs.append("streams")
        return tabs

    def poll(
        self, conn: sqlite3.Connection, subscription_id: int, *, force_backfill: bool = False
    ) -> dict[str, Any]:
        sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if sub is None:
            return {"ok": False, "error": "no such subscription"}
        channel_id = sub["channel_id"]
        all_subs = conn.execute(
            "SELECT * FROM subscriptions WHERE channel_id=? AND enabled=1 ORDER BY id", (channel_id,)
        ).fetchall()
        backfill = force_backfill or not sub["backfill_done"]
        try:
            if backfill:
                result = self._backfill(conn, sub, all_subs)
            else:
                result = self._routine(conn, sub, all_subs)
            status, error = "ok", None
        except DiscoveryError as e:
            self.health.record(conn, e.k, "discovery")
            status, error = e.k.cls, f"{e.k.reason}: {e.k.message}"
            result = {"ok": False, "error": error}
        now = db.now_iso()
        conn.execute(
            "UPDATE subscriptions SET last_polled_at=?, next_poll_at=?, last_poll_status=?, last_poll_error=?"
            " WHERE id=?",
            (now, self.next_poll_at(), status, error, subscription_id),
        )
        if status == "ok":
            fetch_avatar(self.cfg, self.pool, conn, channel_id)
        return result

    def _listing(
        self, conn: sqlite3.Connection, channel_id: str, tabs: list[str], limit: int
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for tab in tabs:
            got, meta = list_channel(self.runner, self.cfg, channel_id, tab, limit)
            if meta.get("id") == channel_id or (meta and not meta.get("id")):
                meta["id"] = channel_id
                upsert_channel_meta(conn, meta)
            entries += got
        self.health.record(conn, C.Classification(C.OK, "ok"), "discovery")
        return entries

    def _backfill(
        self, conn: sqlite3.Connection, sub: sqlite3.Row, all_subs: list[sqlite3.Row]
    ) -> dict[str, Any]:
        n = sub["backfill_count"]
        limit = min(max(n * 3, self.cfg.discovery.flat_listing_limit), 300)
        entries = self._listing(conn, sub["channel_id"], self._tabs([sub]), limit)
        picked = 0
        queued = filtered = 0
        with db.tx(conn):
            for e in entries:
                existing = conn.execute(
                    "SELECT state, filter_reason FROM videos WHERE id=?", (e["id"],)
                ).fetchone()
                verdict = filters.listing_verdict(sub, e)
                wants = verdict.ok and picked < n
                if existing is not None:
                    reevaluable = existing["state"] == "filtered" and (
                        existing["filter_reason"] or ""
                    ).startswith(("title", "before subscription", "duration", "shorts", "streams"))
                    if wants and reevaluable:
                        self._queue_existing(conn, e, sub)
                        picked += 1
                        queued += 1
                    elif wants and existing["state"] in ("queued", "downloading", "cached"):
                        picked += 1
                    continue
                if wants:
                    self._insert(conn, e, sub, "queued", None)
                    picked += 1
                    queued += 1
                else:
                    reason = verdict.reason if not verdict.ok else "before subscription (beyond backfill)"
                    self._insert(conn, e, sub, "filtered", reason)
                    filtered += 1
            conn.execute("UPDATE subscriptions SET backfill_done=1 WHERE id=?", (sub["id"],))
            db.add_event(
                conn,
                "info",
                "discovery",
                f"Backfill for {self._channel_title(conn, sub['channel_id'])}: {queued} queued, {filtered} skipped",
                {"subscription_id": sub["id"], "queued": queued},
            )
        return {"ok": True, "mode": "backfill", "queued": queued, "filtered": filtered}

    def _routine(
        self, conn: sqlite3.Connection, sub: sqlite3.Row, all_subs: list[sqlite3.Row]
    ) -> dict[str, Any]:
        channel_id = sub["channel_id"]
        entries: list[dict[str, Any]] | None = None
        source = "rss"
        if self.cfg.discovery.rss_url_template:
            try:
                xml_text = fetch_rss(self.cfg, channel_id)
                entries = parse_rss(xml_text)
                title = parse_rss_channel(xml_text).get("title")
                if title:
                    conn.execute(
                        "UPDATE channels SET title=COALESCE(title, ?) WHERE id=?", (title, channel_id)
                    )
            except Exception as e:  # noqa: BLE001 - RSS failing just means "use the fallback"
                log.info("RSS for %s failed (%s); falling back to a flat listing", channel_id, e)
                entries = None
        if entries is None:
            source = "flat"
            entries = self._listing(conn, channel_id, self._tabs(all_subs), 30)
        queued = filtered = 0
        with db.tx(conn):
            for e in entries:
                existing = conn.execute(
                    "SELECT state, filter_reason FROM videos WHERE id=?", (e["id"],)
                ).fetchone()
                match = next((s for s in all_subs if filters.listing_verdict(s, e).ok), None)
                if existing is not None:
                    # Title-filtered items may match after the rules were edited. Evicted ones are never
                    # re-queued automatically.
                    if (
                        match
                        and existing["state"] == "filtered"
                        and (existing["filter_reason"] or "").startswith("title")
                    ):
                        self._queue_existing(conn, e, match)
                        queued += 1
                    continue
                if match:
                    self._insert(conn, e, match, "queued", None)
                    queued += 1
                else:
                    self._insert(conn, e, sub, "filtered", filters.listing_verdict(sub, e).reason)
                    filtered += 1
            if queued:
                db.add_event(
                    conn,
                    "info",
                    "discovery",
                    f"{self._channel_title(conn, channel_id)}: {queued} new video(s) queued",
                    {"subscription_id": sub["id"], "queued": queued, "source": source},
                )
        return {"ok": True, "mode": source, "queued": queued, "filtered": filtered, "seen": len(entries)}

    def _channel_title(self, conn: sqlite3.Connection, channel_id: str) -> str:
        row = conn.execute("SELECT title FROM channels WHERE id=?", (channel_id,)).fetchone()
        return (row["title"] if row else None) or channel_id

    def _insert(
        self, conn: sqlite3.Connection, e: dict[str, Any], sub: sqlite3.Row, state: str, reason: str | None
    ) -> None:
        now = db.now_iso()
        conn.execute(
            "INSERT OR IGNORE INTO videos(id, channel_id, title, duration_s, published_at, thumb_url, is_short,"
            " live_status, state, origin, subscription_id, filter_reason, max_height, discovered_at, queued_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,'subscription',?,?,?,?,?)",
            (
                e["id"],
                sub["channel_id"],
                e.get("title"),
                int(e["duration"]) if e.get("duration") else None,
                e.get("published_at"),
                e.get("thumb_url"),
                1 if e.get("is_short") else 0,
                e.get("live_status"),
                state,
                sub["id"],
                reason,
                sub["max_height"],
                now,
                now if state == "queued" else None,
            ),
        )

    def _queue_existing(self, conn: sqlite3.Connection, e: dict[str, Any], sub: sqlite3.Row) -> None:
        conn.execute(
            "UPDATE videos SET state='queued', subscription_id=?, filter_reason=NULL, max_height=?, queued_at=?,"
            " attempts=0, next_attempt_at=NULL WHERE id=?",
            (sub["id"], sub["max_height"], db.now_iso(), e["id"]),
        )


def preview(rules: dict[str, Any], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in entries:
        v = filters.listing_verdict(rules, e)
        out.append({**e, "match": v.ok, "reason": v.reason})
    return out
