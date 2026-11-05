"""Unit tests: filters, VTT cleaning, subtitle choice, RSS parsing, URL parsing, playback, pool order."""

import random
from pathlib import Path

from thytube import db, discovery, filters, playback, subtitles
from thytube.pool import Pool
from thytube.web.yt import parse_url

FIX = Path(__file__).parent / "fixtures"


def test_title_rules() -> None:
    rules = {"include_regex": r"sourdough|rye", "exclude_regex": r"#shorts", "case_sensitive": False}
    assert filters.title_verdict(rules, "SOURDOUGH day").ok
    assert not filters.title_verdict(rules, "Baguette").ok
    v = filters.title_verdict(rules, "rye #shorts")
    assert not v.ok and "exclude" in v.reason
    assert not filters.title_verdict({**rules, "case_sensitive": True}, "SOURDOUGH").ok


def test_validate_regex() -> None:
    assert filters.validate_regex("a(b") is not None
    assert filters.validate_regex("a(b)") is None
    assert filters.validate_regex(None) is None


def test_metadata_rules() -> None:
    rules = {"min_duration_s": 60, "max_duration_s": 3600, "include_shorts": False, "include_streams": False}
    assert not filters.metadata_verdict(rules, {"duration": 30}).ok
    assert not filters.metadata_verdict(rules, {"duration": 7200}).ok
    assert filters.metadata_verdict(rules, {"duration": 600, "live_status": "not_live"}).ok
    assert not filters.metadata_verdict(rules, {"duration": 600, "live_status": "was_live"}).ok
    assert not filters.metadata_verdict({}, {"duration": 50, "width": 1080, "height": 1920}).ok
    assert filters.metadata_verdict(
        {"include_shorts": True}, {"duration": 50, "width": 1080, "height": 1920}
    ).ok


def test_clean_auto_vtt() -> None:
    raw = (FIX / "vtt" / "auto_en.vtt").read_text()
    expected = (FIX / "vtt" / "auto_en.expected.vtt").read_text()
    assert subtitles.clean_vtt(raw, "auto").strip() == expected.strip()


def test_clean_manual_vtt() -> None:
    out = subtitles.clean_vtt((FIX / "vtt" / "manual_ko.vtt").read_text(), "manual")
    assert "안녕하세요" in out and "<c" not in out and "<i>" not in out
    assert "line:90%" not in out
    assert out.startswith("WEBVTT")
    # Manual cues may overlap (two speakers); they're kept as they are.
    assert "00:00:03.000 --> 00:00:05.000" in out


def test_choose_tracks(cfg) -> None:
    info = {
        "subtitles": {"en-GB": [{}], "live_chat": [{}]},
        "automatic_captions": {"ko-orig": [{}], "ko": [{}], "en": [{}], "de": [{}]},
    }
    assert subtitles.choose_tracks(info, cfg) == [("en-GB", "en", "manual"), ("ko-orig", "ko", "auto")]
    # Auto captions in a language that isn't spoken in the video are translations: not wanted by default.
    info2 = {"subtitles": {}, "automatic_captions": {"ja-orig": [{}], "en": [{}], "ko": [{}]}}
    assert subtitles.choose_tracks(info2, cfg) == []
    cfg.subtitles.auto_translated = True
    assert subtitles.choose_tracks(info2, cfg) == [("en", "en", "auto"), ("ko", "ko", "auto")]


def test_parse_rss() -> None:
    entries = discovery.parse_rss((FIX / "rss" / "channel.xml").read_text())
    assert [e["id"] for e in entries] == ["NEWvid00001", "NEWvid00002", "NEWshort001"]
    assert entries[1]["title"] == "LIVE: Q&A night"
    assert entries[2]["is_short"] is True
    assert entries[0]["published_at"] == "2026-11-04T16:00:06Z"


def test_parse_url() -> None:
    assert parse_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10") == ("video", "dQw4w9WgXcQ")
    assert parse_url("youtu.be/dQw4w9WgXcQ") == ("video", "dQw4w9WgXcQ")
    assert parse_url("https://youtube.com/shorts/dQw4w9WgXcQ") == ("video", "dQw4w9WgXcQ")
    assert parse_url("dQw4w9WgXcQ") == ("video", "dQw4w9WgXcQ")
    assert parse_url("https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa") == (
        "channel",
        "UCaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert parse_url("@somebody") == ("channel_url", "https://www.youtube.com/@somebody")
    assert parse_url("https://m.youtube.com/@somebody/videos") == (
        "channel_url",
        "https://www.youtube.com/@somebody",
    )
    assert parse_url("https://example.com/watch?v=dQw4w9WgXcQ") is None
    assert parse_url("cats") is None


def _video(conn, vid, **kw):
    now = db.now_iso()
    cols = {
        "id": vid,
        "state": "cached",
        "origin": "manual",
        "discovered_at": now,
        "channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa",
        "duration_s": 100,
        "size_bytes": 1000,
        "downloaded_at": db.in_iso(days=-30),
        "file_path": f"media/{vid[:2]}/{vid}/video.mp4",
    }
    cols.update(kw)
    conn.execute(
        f"INSERT INTO videos({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(cols.values())
    )


def test_heartbeat_counts_once(cfg, conn) -> None:
    _video(conn, "aaaaaaaaaaa", duration_s=100)
    conn.execute("INSERT INTO profiles(name, created_at) VALUES('Ann', ?)", (db.now_iso(),))
    for _ in range(3):
        r = playback.heartbeat(
            conn, cfg, session_id="s" * 10, video_id="aaaaaaaaaaa", profile_id=1, position=30, played_delta=15
        )
    assert r["played_s"] == 45 and not r["counted"]
    r = playback.heartbeat(
        conn, cfg, session_id="s" * 10, video_id="aaaaaaaaaaa", profile_id=1, position=60, played_delta=15
    )
    assert r["newly_counted"]
    r = playback.heartbeat(
        conn, cfg, session_id="s" * 10, video_id="aaaaaaaaaaa", profile_id=1, position=99, played_delta=15
    )
    assert not r["newly_counted"]
    v = conn.execute("SELECT total_watch_count FROM videos").fetchone()
    w = conn.execute("SELECT watch_count, position_s FROM watch_stats").fetchone()
    assert v[0] == 1 and w["watch_count"] == 1
    assert w["position_s"] == 0  # finished → resume from the start
    # Absurd deltas are clamped.
    r = playback.heartbeat(
        conn, cfg, session_id="t" * 10, video_id="aaaaaaaaaaa", profile_id=1, position=5, played_delta=10_000
    )
    assert r["played_s"] == playback.MAX_DELTA_S


def test_next_weighting(conn) -> None:
    conn.execute("INSERT INTO profiles(name, created_at) VALUES('Ann', ?)", (db.now_iso(),))
    for vid in ("cur00000000", "aaa00000000", "bbb00000000"):
        _video(conn, vid)
    conn.execute("INSERT INTO watch_stats(profile_id, video_id, watch_count) VALUES(1, 'bbb00000000', 3)")
    rng = random.Random(1)
    picks = [playback.pick_next(conn, "cur00000000", 1, [], rng)["id"] for _ in range(3000)]
    share = picks.count("aaa00000000") / len(picks)
    assert 0.62 < share < 0.71  # weight 2 vs 1 → 2/3
    p = playback.pick_next(conn, "cur00000000", 1, ["aaa00000000"], rng)
    assert p["id"] == "bbb00000000"
    p = playback.pick_next(conn, "cur00000000", 1, ["aaa00000000", "bbb00000000"], rng)
    assert p["reset"] is True


def test_eviction_order(cfg, conn) -> None:
    _video(conn, "watched3xxx", total_watch_count=3, last_watched_at=db.in_iso(days=-1))
    _video(conn, "watched1old", total_watch_count=1, last_watched_at=db.in_iso(days=-20))
    _video(conn, "watched1new", total_watch_count=1, last_watched_at=db.in_iso(days=-2))
    old = db.in_iso(days=-40)
    _video(conn, "unwatchedol", total_watch_count=0, downloaded_at=old, size_bytes=5)
    _video(conn, "unwatchedbg", total_watch_count=0, downloaded_at=old, size_bytes=50)
    _video(conn, "freshxxxxxx", total_watch_count=0, downloaded_at=db.in_iso(days=-1))
    _video(conn, "pinnedxxxxx", total_watch_count=0, pinned=1)
    _video(conn, "playingxxxx", total_watch_count=0)
    conn.execute(
        "INSERT INTO play_sessions(id, video_id, started_at, last_heartbeat_at) VALUES('p1','playingxxxx',?,?)",
        (db.now_iso(), db.now_iso()),
    )
    order = [r["id"] for r in Pool(cfg).eviction_candidates(conn)]
    assert order == ["unwatchedbg", "unwatchedol", "watched1old", "watched1new", "watched3xxx", "freshxxxxxx"]
