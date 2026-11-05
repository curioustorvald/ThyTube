"""Integration tests: the download pipeline, pool admission, health/canary/updater, discovery."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from thytube import db
from thytube.discovery import Discovery
from thytube.downloader import Downloader
from thytube.health import Health, run_canary
from thytube.pool import Pool
from thytube.ytdlp import updater
from thytube.ytdlp.runner import Runner

FIX = Path(__file__).parent / "fixtures"
CH = "UCaaaaaaaaaaaaaaaaaaaaaa"


def make(cfg):
    runner = Runner(cfg)
    pool = Pool(cfg)
    health = Health(cfg)
    return Downloader(cfg, runner, pool, health), pool, health


def queue(conn, vid, origin="manual", **kw):
    cols = {
        "id": vid,
        "state": "queued",
        "origin": origin,
        "discovered_at": db.now_iso(),
        "queued_at": db.now_iso(),
    }
    cols.update(kw)
    conn.execute(
        f"INSERT INTO videos({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(cols.values())
    )


def state(conn, vid):
    return conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()


def test_download_end_to_end(cfg, conn, scenario) -> None:
    dl, pool, _ = make(cfg)
    queue(conn, "vidAAAAAAAA")
    out = dl.process(conn, "vidAAAAAAAA", threading.Event())
    assert out.state == "cached", out
    row = state(conn, "vidAAAAAAAA")
    media = cfg.pool_dir / row["file_path"]
    assert media.is_file() and row["size_bytes"] == media.stat().st_size
    assert (media.parent / "thumb.jpg").is_file()
    assert row["vcodec"] == "h264" and row["acodec"] == "aac"
    assert row["reserved_bytes"] is None and row["title"]
    assert (
        row["channel_id"]
        and conn.execute("SELECT title FROM channels WHERE id=?", (row["channel_id"],)).fetchone()
    )
    subs = conn.execute("SELECT lang, kind, path FROM subtitles").fetchall()
    assert [(s["lang"], s["kind"]) for s in subs] == [("en", "manual")]
    assert (cfg.pool_dir / subs[0]["path"]).read_text().startswith("WEBVTT")
    assert row["subs_state"] == "done"
    assert not any(cfg.partial_dir.iterdir())
    # The download reused the metadata (no second extraction).
    dl_calls = [c for c in scenario.calls() if "--load-info-json" in c and "--skip-download" not in c]
    assert len(dl_calls) == 1


def test_subtitle_failure_is_not_fatal(cfg, conn, scenario) -> None:
    scenario({"videos": {"vidBBBBBBBB": {"subs_mode": "429"}}})
    dl, _, _ = make(cfg)
    queue(conn, "vidBBBBBBBB")
    assert dl.process(conn, "vidBBBBBBBB", threading.Event()).state == "cached"
    row = state(conn, "vidBBBBBBBB")
    assert row["subs_state"] == "pending" and row["subs_next_at"]


def test_unavailable(cfg, conn, scenario) -> None:
    scenario({"videos": {"vidCCCCCCCC": {"mode": "private"}}})
    dl, _, _ = make(cfg)
    queue(conn, "vidCCCCCCCC")
    assert dl.process(conn, "vidCCCCCCCC", threading.Event()).state == "unavailable"
    assert "private" in state(conn, "vidCCCCCCCC")["last_error"]


def test_transient_backoff_then_failed(cfg, conn, scenario) -> None:
    scenario({"videos": {"vidDDDDDDDD": {"download_mode": "network"}}})
    dl, _, _ = make(cfg)
    queue(conn, "vidDDDDDDDD")
    assert dl.process(conn, "vidDDDDDDDD", threading.Event()).state == "queued"
    row = state(conn, "vidDDDDDDDD")
    assert row["attempts"] == 1 and row["next_attempt_at"] > db.now_iso()
    for _ in range(cfg.ytdlp.max_attempts - 1):
        out = dl.process(conn, "vidDDDDDDDD", threading.Event())
    assert out.state == "failed"
    assert state(conn, "vidDDDDDDDD")["reserved_bytes"] is None


def test_systemic_blocks_without_spending_attempts(cfg, conn, scenario) -> None:
    scenario({"mode": "botcheck"})
    dl, _, health = make(cfg)
    for i in range(4):
        vid = f"vidSYS{i:05d}"
        queue(conn, vid)
        assert dl.process(conn, vid, threading.Event()).state == "queued"
        assert state(conn, vid)["attempts"] == 0
    h = health.get(conn)
    assert h["state"] == "blocked" and not health.allows_youtube(conn)
    assert h["next_probe_at"]
    # Entering "blocked" asks for a yt-dlp update (none has run yet).
    assert conn.execute("SELECT kind FROM commands").fetchone()["kind"] == "update_ytdlp"
    # Canary fails while blocked → backoff doubles.
    result = run_canary(cfg, Runner(cfg))
    assert not result["ok"] and result["reason"] == "bot_check"
    health.on_probe(conn, result)
    assert health.get(conn)["probe_backoff_minutes"] == 30
    # Canary passes → ok, held items released.
    scenario({})
    result = run_canary(cfg, Runner(cfg))
    assert result["ok"], result
    health.on_probe(conn, result)
    assert health.get(conn)["state"] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE next_attempt_at IS NOT NULL").fetchone()[0] == 0


def test_admission_evicts_then_defers(cfg, conn, scenario) -> None:
    # Budget 10 MB (conftest). Fill it with two 4.5 MB cached videos, one watched.
    dl, pool, _ = make(cfg)
    for vid, watched in (("oldWATCHED1", 2), ("oldUNWATCH1", 0)):
        d = cfg.pool_dir / "media" / vid[:2] / vid
        d.mkdir(parents=True)
        (d / "video.mp4").write_bytes(b"x" * 4_500_000)
        (d / "thumb.jpg").write_bytes(b"j")
        queue(
            conn,
            vid,
            state="cached",
            size_bytes=4_500_000,
            file_path=f"media/{vid[:2]}/{vid}/video.mp4",
            downloaded_at=db.in_iso(days=-30),
            total_watch_count=watched,
        )
    scenario({"videos": {"newVIDEO001": {"filesize": 2_000_000}}})
    queue(conn, "newVIDEO001")
    assert dl.process(conn, "newVIDEO001", threading.Event()).state == "cached"
    assert state(conn, "oldUNWATCH1")["state"] == "evicted"
    assert state(conn, "oldWATCHED1")["state"] == "cached"
    assert (cfg.pool_dir / "media/ol/oldUNWATCH1/thumb.jpg").exists()
    assert not (cfg.pool_dir / "media/ol/oldUNWATCH1/video.mp4").exists()
    u = pool.usage(conn)
    assert u["cached_bytes"] + u["reserved_bytes"] <= cfg.pool.max_bytes

    # Something bigger than what could ever be freed: deferred, nothing evicted.
    conn.execute("UPDATE videos SET pinned=1 WHERE id='oldWATCHED1'")
    scenario({"videos": {"bigVIDEO001": {"filesize": 9_000_000}}})
    queue(conn, "bigVIDEO001")
    assert dl.process(conn, "bigVIDEO001", threading.Event()).state == "deferred_space"
    assert state(conn, "oldWATCHED1")["state"] == "cached"
    assert state(conn, "bigVIDEO001")["reserved_bytes"] is None


def test_subscription_rules_at_download(cfg, conn, scenario) -> None:
    conn.execute("INSERT INTO channels(id) VALUES(?)", (CH,))
    conn.execute(
        "INSERT INTO subscriptions(channel_id, created_at, min_duration_s) VALUES(?, ?, 60)",
        (CH, db.now_iso()),
    )
    scenario({"videos": {"shortVID001": {"duration": 3}}})
    queue(conn, "shortVID001", origin="subscription", subscription_id=1)
    dl, _, _ = make(cfg)
    out = dl.process(conn, "shortVID001", threading.Event())
    assert out.state == "filtered" and "shorter" in out.detail


def test_cancel(cfg, conn, scenario) -> None:
    scenario({"videos": {"slowVIDEO01": {"download_mode": "slow"}}, "slow_seconds": 20})
    dl, _, _ = make(cfg)
    queue(conn, "slowVIDEO01")
    cancel = threading.Event()
    threading.Timer(1.5, cancel.set).start()
    out = dl.process(conn, "slowVIDEO01", cancel)
    assert out.state == "queued" and out.detail == "cancelled"
    assert state(conn, "slowVIDEO01")["attempts"] == 0
    assert not (cfg.partial_dir / "slowVIDEO01").exists()


def test_reconcile(cfg, conn) -> None:
    pool = Pool(cfg)
    queue(conn, "goneVIDEO01", state="cached", size_bytes=10, file_path="media/go/goneVIDEO01/video.mp4")
    d = cfg.pool_dir / "media/xx/xxORPHAN001"
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(b"x")
    (cfg.partial_dir / "staleVIDEO1").mkdir()
    rep = pool.reconcile(conn)
    assert rep["missing"] == ["goneVIDEO01"] and rep["orphans"] == ["xxORPHAN001"]
    assert rep["stale_partials"] == ["staleVIDEO1"]
    assert state(conn, "goneVIDEO01")["state"] == "evicted"
    assert not d.exists()


# ---- discovery -----------------------------------------------------------------------


def subscribe(conn, **rules):
    conn.execute("INSERT OR IGNORE INTO channels(id) VALUES(?)", (CH,))
    cols = {"channel_id": CH, "created_at": db.now_iso(), **rules}
    cur = conn.execute(
        f"INSERT INTO subscriptions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        tuple(cols.values()),
    )
    return cur.lastrowid


def test_backfill_then_rss(cfg, conn, scenario, monkeypatch) -> None:
    scenario({"channels": {CH: {"title": "Bread & Butter", "count": 20}}})
    disc = Discovery(cfg, Runner(cfg), Pool(cfg), Health(cfg))
    sid = subscribe(conn, backfill_count=3)
    res = disc.poll(conn, sid)
    assert res == {"ok": True, "mode": "backfill", "queued": 3, "filtered": 17}
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE state='queued'").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 20
    assert conn.execute("SELECT title FROM channels WHERE id=?", (CH,)).fetchone()[0] == "Bread & Butter"

    # Routine poll via RSS (mocked; tests never hit the network).
    cfg.discovery.rss_url_template = "https://example.invalid/{channel_id}"
    monkeypatch.setattr(
        "thytube.discovery.fetch_rss", lambda cfg, cid: (FIX / "rss" / "channel.xml").read_text()
    )
    conn.execute("UPDATE subscriptions SET exclude_regex='^LIVE' WHERE id=?", (sid,))
    res = disc.poll(conn, sid)
    assert res["mode"] == "rss" and res["queued"] == 1
    assert state(conn, "NEWvid00001")["state"] == "queued"
    assert state(conn, "NEWvid00002")["filter_reason"].startswith("title")
    assert state(conn, "NEWshort001")["filter_reason"] == "shorts excluded"
    # Evicted videos are never re-queued by polling.
    conn.execute("UPDATE videos SET state='evicted' WHERE id='NEWvid00001'")
    disc.poll(conn, sid)
    assert state(conn, "NEWvid00001")["state"] == "evicted"


def test_rss_failure_falls_back_to_listing(cfg, conn, scenario, monkeypatch) -> None:
    scenario({"channels": {CH: {"count": 5}}})
    disc = Discovery(cfg, Runner(cfg), Pool(cfg), Health(cfg))
    sid = subscribe(conn, backfill_count=0, backfill_done=1)
    cfg.discovery.rss_url_template = "https://example.invalid/{channel_id}"

    def boom(*a):
        raise OSError("HTTP Error 404")

    monkeypatch.setattr("thytube.discovery.fetch_rss", boom)
    res = disc.poll(conn, sid)
    assert res["mode"] == "flat" and res["queued"] == 5


def test_discovery_failure_feeds_health(cfg, conn, scenario) -> None:
    scenario({"mode": "botcheck"})
    disc = Discovery(cfg, Runner(cfg), Pool(cfg), Health(cfg))
    sid = subscribe(conn)
    disc.poll(conn, sid)
    sub = conn.execute("SELECT * FROM subscriptions").fetchone()
    assert sub["last_poll_status"] == "systemic" and sub["next_poll_at"]
    assert Health(cfg).get(conn)["consecutive_systemic"] == 1


# ---- updater --------------------------------------------------------------------------


def fake_installer(mode: str = "ok", version: str = "2099.01.02"):
    fake = Path(__file__).parent / "fake_ytdlp.py"

    def install(cfg, venv: Path) -> None:
        (venv / "bin").mkdir(parents=True)
        script = venv / "bin" / "yt-dlp"
        script.write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = --version ]; then echo {version}; exit 0; fi\n'
            + (f"FAKE_YTDLP_MODE={mode} " if mode != "ok" else "")
            + f'exec {os.sys.executable} {fake} "$@"\n'
        )
        script.chmod(0o755)

    return install


def test_updater_install_swap_and_rollback(cfg, conn, scenario) -> None:
    cfg.ytdlp.binary = ""
    r1 = updater.update(cfg, installer=fake_installer(version="2099.01.01"))
    assert r1["outcome"] == "installed", r1
    first = updater.current_venv(cfg)
    assert cfg.ytdlp_binary.exists()

    # Same version → unchanged, the temporary venv is removed.
    r = updater.update(cfg, installer=fake_installer(version="2099.01.01"))
    assert r["outcome"] == "unchanged" and len(updater.venvs(cfg)) == 1

    # A new version that fails the canary while the old one passes → kept (automatic rollback).
    r = updater.update(cfg, installer=fake_installer(mode="botcheck", version="2099.02.01"))
    assert r["outcome"] == "kept" and updater.current_venv(cfg) == first
    assert len(updater.venvs(cfg)) == 1

    # A working new version is installed.
    r = updater.update(cfg, installer=fake_installer(version="2099.03.01"))
    assert r["outcome"] == "installed" and updater.current_venv(cfg) != first
    info = db.kv_get(conn, "ytdlp")
    assert info["version"] == "2099.03.01" and len(info["history"]) == 4

    r = updater.rollback(cfg)
    assert r["ok"] and r["active_version"] == "2099.01.01"
    assert db.kv_get(conn, "ytdlp_updating") is None
