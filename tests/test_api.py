"""HTTP API tests, with the worker driven tick by tick."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from thytube import db
from thytube.web.app import create_app
from thytube.worker import Worker

CH = "UCaaaaaaaaaaaaaaaaaaaaaa"


@pytest.fixture
def client(cfg, conn):
    return TestClient(create_app(cfg))


@pytest.fixture
def worker(cfg):
    w = Worker(cfg)
    w.conn = db.open_db(cfg.db_path)
    w.startup()
    yield w
    w.stop.set()
    w.shutdown()


def drain(worker: Worker, timeout: float = 60) -> None:
    """Tick until no downloads/background tasks are running and nothing is due."""
    deadline = time.monotonic() + timeout
    idle_ticks = 0
    while time.monotonic() < deadline:
        worker.tick()
        busy = worker.active or worker.bg is not None
        due = worker.conn.execute(
            "SELECT COUNT(*) FROM videos WHERE state='queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)",
            (db.now_iso(),),
        ).fetchone()[0]
        pending = worker.conn.execute("SELECT COUNT(*) FROM commands WHERE taken_at IS NULL").fetchone()[0]
        idle_ticks = idle_ticks + 1 if not (busy or due or pending) else 0
        if idle_ticks >= 2:
            return
        time.sleep(0.1)
    raise AssertionError("worker did not settle")


def test_errors_are_uniform(client) -> None:
    r = client.get("/api/videos/not-an-id")
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_video_id"
    r = client.get("/api/videos?sort=nope")
    assert r.json()["error"]["code"] == "bad_sort"
    r = client.get("/api/nope")
    assert r.status_code == 404 and "error" in r.json()


def test_profiles(client) -> None:
    r = client.post("/api/profiles", json={"name": "Ann", "colour": "#ff0000"})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert client.post("/api/profiles", json={"name": "Ann"}).status_code == 409
    assert client.patch(f"/api/profiles/{pid}", json={"name": "Anna"}).json()["name"] == "Anna"
    assert [p["name"] for p in client.get("/api/profiles").json()["items"]] == ["Anna"]
    assert client.delete(f"/api/profiles/{pid}").status_code == 200


def test_manual_cache_play_and_delete(client, worker, cfg) -> None:
    pid = client.post("/api/profiles", json={"name": "Ann"}).json()["id"]
    h = {"X-Profile": str(pid)}
    r = client.post("/api/videos/vidAPI00001/cache", json={"title": "Pending title"}, headers=h)
    assert r.status_code == 202 and r.json()["state"] == "queued"
    assert client.get("/api/queue").json()["queued"][0]["id"] == "vidAPI00001"
    drain(worker)

    v = client.get("/api/videos/vidAPI00001", headers=h).json()
    assert v["state"] == "cached" and v["media_url"].endswith("/video.mp4")
    assert v["subtitles"] and v["subtitles"][0]["url"].endswith(".vtt")
    assert v["chapters"] and v["me"]["position_s"] == 0
    # Range requests work for the media (Apache in production; StaticFiles in dev).
    r = client.get(v["media_url"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert client.get(v["subtitles"][0]["url"]).text.startswith("WEBVTT")

    lib = client.get("/api/videos", headers=h).json()
    assert [i["id"] for i in lib["items"]] == ["vidAPI00001"]
    assert client.get("/api/channels").json()["items"][0]["cached_count"] == 1

    # Heartbeat via a sendBeacon-style text/plain body.
    body = json.dumps(
        {"session": "sess-123456", "video": "vidAPI00001", "profile": pid, "position": 1.5, "played_delta": 2}
    )
    r = client.post("/api/playback/heartbeat", content=body, headers={"Content-Type": "text/plain"})
    assert r.json()["ok"] and r.json()["newly_counted"]  # 3 s video: threshold 1.5 s
    assert client.get("/api/videos/continue", headers=h).json()["items"] == []  # position < 5 s

    assert client.post("/api/videos/vidAPI00001/pin").json()["pinned"] is True
    assert client.get("/api/storage").json()["usage"]["pinned_bytes"] > 0

    assert client.delete("/api/videos/vidAPI00001/files").status_code == 202
    drain(worker)
    v = client.get("/api/videos/vidAPI00001").json()
    assert v["state"] == "evicted" and v["media_url"] is None
    assert v["thumb"].endswith("thumb.jpg")  # kept for the library
    # Evicted is hidden by default, shown with state=library.
    assert client.get("/api/videos").json()["items"] == []
    assert len(client.get("/api/videos?state=library").json()["items"]) == 1
    # "Cache again".
    assert client.post("/api/videos/vidAPI00001/cache").json()["state"] == "queued"


def test_pin_limit(client, conn, cfg) -> None:
    cfg.pool.max_pinned_ratio = 0.1  # 1 MB of the 10 MB test budget
    conn.execute(
        "INSERT INTO videos(id, state, origin, discovered_at, size_bytes) VALUES('bigPIN00001','cached','manual',?,2000000)",
        (db.now_iso(),),
    )
    r = client.post("/api/videos/bigPIN00001/pin")
    assert r.status_code == 409 and r.json()["error"]["code"] == "pin_limit"


def test_pagination(client, conn) -> None:
    for i in range(7):
        conn.execute(
            "INSERT INTO videos(id, title, state, origin, discovered_at, published_at) VALUES(?,?,?,?,?,?)",
            (f"page{i:07d}", f"t{i}", "cached", "manual", db.now_iso(), f"2026-01-0{i + 1}T00:00:00Z"),
        )
    seen, cursor = [], None
    while True:
        r = client.get("/api/videos", params={"limit": 3, **({"cursor": cursor} if cursor else {})}).json()
        seen += [i["id"] for i in r["items"]]
        cursor = r["next_cursor"]
        if not cursor:
            break
    assert seen == [f"page{i:07d}" for i in range(6, -1, -1)]
    assert [i["id"] for i in client.get("/api/videos?q=t3").json()["items"]] == ["page0000003"]


def test_subscription_flow(client, worker, scenario) -> None:
    scenario({"channels": {CH: {"title": "Bread & Butter", "count": 12}}})
    r = client.post("/api/subscriptions", json={"channel_id": CH, "include_regex": "(unclosed"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_regex"
    assert r.json()["error"]["field"] == "include_regex"

    p = client.post("/api/subscriptions/preview", json={"channel_id": CH, "max_duration_s": 2}).json()
    assert p["total"] == 12 and p["matched"] == 0 and "duration" in p["items"][0]["reason"]

    r = client.post(
        "/api/subscriptions",
        json={
            "channel": {"id": CH, "title": "Bread & Butter"},
            "backfill_count": 2,
            "max_height": 720,
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    drain(worker)
    subs = client.get("/api/subscriptions").json()["items"]
    assert subs[0]["cached_count"] == 2 and subs[0]["backfill_done"]
    ch = client.get(f"/api/channels/{CH}").json()
    assert ch["subscribed"] and ch["subscriptions"][0]["max_height"] == 720

    # Raising the backfill later fetches more of the back catalogue.
    assert client.patch(f"/api/subscriptions/{sid}", json={"backfill_count": 4}).json()["backfill_count"] == 4
    drain(worker)
    assert client.get("/api/subscriptions").json()["items"][0]["cached_count"] == 4

    assert (
        client.patch(
            f"/api/subscriptions/{sid}", json={"exclude_regex": "x[", "case_sensitive": True}
        ).status_code
        == 422
    )
    assert client.patch(f"/api/subscriptions/{sid}", json={"enabled": False}).json()["enabled"] is False

    r = client.delete(f"/api/subscriptions/{sid}?delete_files=true").json()
    assert r["deleting"] == 4
    drain(worker)
    assert client.get("/api/videos").json()["items"] == []
    assert client.get("/api/subscriptions").json()["items"] == []


def test_search_and_resolve(client, conn) -> None:
    conn.execute(
        "INSERT INTO videos(id, title, state, origin, discovered_at) VALUES(?, 'x', 'cached', 'manual', ?)",
        ("dQw4w9WgXcQ", db.now_iso()),
    )
    items = client.get("/api/search", params={"q": "bread"}).json()["items"]
    assert len(items) == 20 and all(i["type"] == "video" for i in items)
    chans = client.get("/api/search", params={"q": "bread", "type": "channel"}).json()["items"]
    assert chans and chans[0]["type"] == "channel" and chans[0]["subscribed"] is False

    r = client.get("/api/resolve", params={"url": "https://youtu.be/dQw4w9WgXcQ"}).json()
    assert r["type"] == "video" and r["video"]["state"] == "cached"
    r = client.get("/api/resolve", params={"url": "https://www.youtube.com/watch?v=abcdefghijk"}).json()
    assert r["video"]["title"] and r["video"]["state"] is None
    r = client.get("/api/resolve", params={"url": "@breadandbutter"}).json()
    assert r["type"] == "channel" and r["channel"]["id"].startswith("UC")
    assert client.get("/api/resolve", params={"url": "https://example.com"}).status_code == 422


def test_search_failure_reports_health(client, scenario, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_YTDLP_MODE", "botcheck")
    for q in ("a", "b"):
        r = client.get("/api/search", params={"q": q})
        assert r.status_code == 502 and r.json()["error"]["code"] == "youtube_systemic"
    s = client.get("/api/status").json()
    assert s["health"]["state"] == "degraded"
    assert "failing" in s["health"]["explanation"]


def test_status_and_admin(client, worker) -> None:
    worker.tick()
    s = client.get("/api/status").json()
    assert s["worker"]["alive"] and s["health"]["state"] == "ok"
    assert s["pool"]["max_bytes"] == 10_000_000
    cmd = client.post("/api/admin/probe").json()["command_id"]
    drain(worker)
    assert client.get(f"/api/commands/{cmd}").json()["done_at"]
    ev = client.get("/api/events").json()["items"]
    assert any(e["kind"] == "probe" for e in ev)


def test_queue_retry_and_cancel(client, conn) -> None:
    conn.execute(
        "INSERT INTO videos(id, state, origin, discovered_at, attempts) VALUES('failVIDEO01','failed','manual',?,5)",
        (db.now_iso(),),
    )
    assert client.get("/api/queue").json()["failed"][0]["id"] == "failVIDEO01"
    assert client.post("/api/queue/failVIDEO01/retry").json()["ok"]
    assert conn.execute("SELECT state, attempts FROM videos").fetchone()[:] == ("queued", 0)
    assert client.delete("/api/queue/failVIDEO01").json()["ok"]
    assert client.delete("/api/queue/failVIDEO01").status_code == 409


def test_next_endpoint(client, conn) -> None:
    for vid in ("nextCUR0001", "nextOTHER01"):
        conn.execute(
            "INSERT INTO videos(id, channel_id, state, origin, discovered_at) VALUES(?,?,'cached','manual',?)",
            (vid, CH, db.now_iso()),
        )
    r = client.get("/api/next", params={"video": "nextCUR0001"}).json()
    assert r["video"]["id"] == "nextOTHER01"
    r = client.get("/api/next", params={"video": "nextCUR0001", "exclude": "nextOTHER01"}).json()
    assert r["video"]["id"] == "nextOTHER01" and r["reset"]
