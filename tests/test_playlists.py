"""Playlists: hand-made ones, YouTube imports, syncing, and playback order."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_api import drain, worker  # noqa: F401  (worker is a fixture)

from thytube import db
from thytube.web import yt
from thytube.web.app import create_app

PL = "PLfakefakefakefake01"
CH = "UCbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def client(cfg, conn):
    yt._cache._d.clear()  # listings are cached per process
    return TestClient(create_app(cfg))


def _cached(conn, vid: str, title: str = "x") -> None:
    conn.execute(
        "INSERT INTO videos(id, title, state, origin, file_path, discovered_at) VALUES(?,?, 'cached', 'manual', ?, ?)",
        (vid, title, f"media/{vid[:2]}/{vid}/video.mp4", db.now_iso()),
    )


def test_parse_playlist_urls() -> None:
    assert yt.parse_playlist(f"https://www.youtube.com/playlist?list={PL}") == PL
    assert yt.parse_playlist(f"youtube.com/watch?v=abcdefghijk&list={PL}&index=3") == PL
    assert yt.parse_playlist(PL) == PL
    assert yt.parse_playlist("https://example.com/playlist?list=" + PL) is None
    assert yt.parse_playlist("WL") is None
    assert yt.parse_url(f"https://www.youtube.com/playlist?list={PL}") == ("playlist", PL)
    # A watch URL inside a playlist still opens the video.
    assert yt.parse_url(f"https://www.youtube.com/watch?v=abcdefghijk&list={PL}") == ("video", "abcdefghijk")


def test_hand_made_playlist(client, conn) -> None:
    pid_profile = client.post("/api/profiles", json={"name": "Ann"}).json()["id"]
    h = {"X-Profile": str(pid_profile)}
    for vid in ("aaaaaaaaaa1", "aaaaaaaaaa2", "aaaaaaaaaa3"):
        _cached(conn, vid)
    assert client.post("/api/playlists", json={"title": "  "}).status_code == 422
    p = client.post(
        "/api/playlists", json={"title": "Evening", "video_ids": ["aaaaaaaaaa1"]}, headers=h
    ).json()
    assert p["count"] == 1 and p["owner"]["name"] == "Ann" and p["source"] is None

    r = client.post(f"/api/playlists/{p['id']}/items", json={"video_id": "aaaaaaaaaa2"}).json()
    assert r["added"] and r["playlist"]["count"] == 2
    assert not client.post(f"/api/playlists/{p['id']}/items", json={"video_id": "aaaaaaaaaa2"}).json()[
        "added"
    ]
    # An unknown video needs its particulars, and is remembered without being cached.
    assert client.post(f"/api/playlists/{p['id']}/items", json={"video_id": "zzzzzzzzzz1"}).status_code == 404
    r = client.post(
        f"/api/playlists/{p['id']}/items",
        json={"video_id": "zzzzzzzzzz1", "title": "From search", "channel": {"id": CH, "title": "Bee"}},
    ).json()
    assert r["playlist"]["count"] == 3 and r["playlist"]["cached_count"] == 2
    v = client.get("/api/videos/zzzzzzzzzz1").json()
    assert v["state"] == "discovered" and v["origin"] == "playlist" and v["channel"]["title"] == "Bee"

    lists = client.get("/api/playlists", params={"video": "aaaaaaaaaa2"}).json()["items"]
    assert lists[0]["contains"] is True
    assert (
        client.get("/api/playlists", params={"video": "aaaaaaaaaa3"}).json()["items"][0]["contains"] is False
    )

    order = client.put(f"/api/playlists/{p['id']}/order", json={"video_ids": ["zzzzzzzzzz1", "aaaaaaaaaa1"]})
    assert order.json()["video_ids"] == ["zzzzzzzzzz1", "aaaaaaaaaa1", "aaaaaaaaaa2"]
    detail = client.get(f"/api/playlists/{p['id']}", headers=h).json()
    assert [i["id"] for i in detail["items"]] == ["zzzzzzzzzz1", "aaaaaaaaaa1", "aaaaaaaaaa2"]
    assert "me" in detail["items"][0]

    assert client.patch(f"/api/playlists/{p['id']}", json={"title": "Night"}).json()["title"] == "Night"

    # Removing the only reference to a video known just from the playlist forgets it.
    client.delete(f"/api/playlists/{p['id']}/items/zzzzzzzzzz1")
    assert client.get("/api/videos/zzzzzzzzzz1").status_code == 404
    assert client.delete(f"/api/playlists/{p['id']}").status_code == 200
    assert client.get(f"/api/playlists/{p['id']}").status_code == 404
    assert client.get("/api/videos/aaaaaaaaaa1").json()["state"] == "cached"  # videos stay


def test_import_from_youtube_and_cache(client, conn, scenario, worker) -> None:  # noqa: F811
    _cached(conn, "aaaaaaaaaa1")
    scenario(
        {
            "playlists": {
                PL: {
                    "title": "Bread lore",
                    "owner": "Baker",
                    "video_ids": ["aaaaaaaaaa1", "plvid000001", "plvid000002"],
                    "private": 2,
                }
            }
        }
    )
    url = f"https://www.youtube.com/playlist?list={PL}"
    pre = client.get("/api/playlists/youtube", params={"url": url}).json()
    assert pre["title"] == "Bread lore" and pre["count"] == 3 and pre["cached"] == 1  # private ones dropped
    assert pre["imported_as"] is None and pre["items"][0]["state"] == "cached"
    r = client.get("/api/resolve", params={"url": url}).json()
    assert r["type"] == "playlist" and r["playlist"]["count"] == 3

    assert client.post("/api/playlists/import", json={"url": "https://example.com"}).status_code == 422
    p = client.post("/api/playlists/import", json={"url": url, "cache": False}).json()
    assert p["added"] == 3 and p["source"]["id"] == PL and p["cached_count"] == 1 and not p["auto_cache"]
    states = {i["id"]: i["state"] for i in client.get(f"/api/playlists/{p['id']}").json()["items"]}
    assert states == {"aaaaaaaaaa1": "cached", "plvid000001": "discovered", "plvid000002": "discovered"}
    again = client.post("/api/playlists/import", json={"url": url})
    assert again.status_code == 409 and again.json()["error"]["playlist_id"] == p["id"]

    # Cache the rest; they queue at normal priority and get downloaded.
    assert client.post(f"/api/playlists/{p['id']}/cache").json()["queued"] == 2
    drain(worker)
    p2 = client.get(f"/api/playlists/{p['id']}").json()
    assert p2["cached_count"] == 3
    assert {i["origin"] for i in p2["items"] if i["id"] != "aaaaaaaaaa1"} == {"playlist"}

    # Sync appends what's new on YouTube (and queues it when the playlist asks for that).
    client.patch(f"/api/playlists/{p['id']}", json={"auto_cache": True})
    scenario(
        {
            "playlists": {
                PL: {"title": "Bread lore II", "video_ids": ["plvid000003", "aaaaaaaaaa1", "plvid000001"]}
            }
        }
    )
    s = client.post(f"/api/playlists/{p['id']}/sync").json()
    assert s["added"] == 1 and s["playlist"]["source"]["title"] == "Bread lore II"
    ids = [i["id"] for i in client.get(f"/api/playlists/{p['id']}").json()["items"]]
    assert ids == ["aaaaaaaaaa1", "plvid000001", "plvid000002", "plvid000003"]
    assert client.get("/api/videos/plvid000003").json()["state"] == "queued"


def test_subscription_takes_over_playlist_only_videos(cfg, conn) -> None:
    """A video known only from a playlist must not block a subscription from queuing it."""
    from thytube.discovery import Discovery
    from thytube.health import Health
    from thytube.pool import Pool
    from thytube.ytdlp.runner import Runner

    now = db.now_iso()
    conn.execute("INSERT INTO channels(id, title) VALUES(?, 'C')", (CH,))
    conn.execute(
        "INSERT INTO subscriptions(channel_id, backfill_count, created_at) VALUES(?, 3, ?)", (CH, now)
    )
    from fake_ytdlp import fake_id

    first = fake_id(f"{CH}-videos-0")
    conn.execute(
        "INSERT INTO videos(id, channel_id, state, origin, discovered_at) VALUES(?,?, 'discovered', 'playlist', ?)",
        (first, CH, now),
    )
    d = Discovery(cfg, Runner(cfg), Pool(cfg), Health(cfg))
    d.poll(conn, 1)
    row = conn.execute("SELECT state, origin, subscription_id FROM videos WHERE id=?", (first,)).fetchone()
    assert (row["state"], row["origin"], row["subscription_id"]) == ("queued", "subscription", 1)
