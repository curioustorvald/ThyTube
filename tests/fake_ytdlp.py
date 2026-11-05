#!/usr/bin/env python3
"""A stand-in for the yt-dlp executable, for tests and local development. Never touches the network.

Point ``ytdlp.binary`` at this file. Behaviour is driven by environment variables:

  FAKE_YTDLP_MODE      global mode for every call: ok (default) | botcheck | 403 | 429 | network |
                       private | removed | members | upcoming | format | slow | hang
  FAKE_YTDLP_SCENARIO  path to a JSON file: {"mode": "...", "videos": {"<id>": {"mode": "...",
                       "duration": 12, "title": "...", "channel_id": "UC…", "live_status": "...",
                       "subtitles": ["en"], "auto_captions": ["ko"], "filesize": 123,
                       "download_mode": "...", "subs_mode": "..."}},  (mode = metadata call)
                       "channels": {"UC…": {"title": "...", "handle": "@x", "count": 30}},
                       "version": "2099.01.01"}. Re-read on every call, so tests can change it.
  FAKE_YTDLP_LOG       append one JSON line per invocation (argv) to this file
  FAKE_YTDLP_SAMPLE_DIR  where generated sample MP4s are cached (default ~/.cache/thytube-fake)
  FAKE_YTDLP_DURATION  default duration in seconds for generated videos (default 6)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ytdlp_stderr"
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

MODE_FIXTURE = {
    "botcheck": "systemic__bot_check__1",
    "403": "systemic__forbidden__1",
    "format": "systemic__format_unavailable__1",
    "jsc": "systemic__js_challenge__2",
    "429": "transient__rate_limited__1",
    "network": "transient__network__1",
    "private": "video__private__1",
    "removed": "video__removed__1",
    "members": "video__members_only__1",
    "upcoming": "video__upcoming__1",
    "geo": "video__geo_blocked__1",
}

TOPICS = [
    "Woodworking",
    "Synth repair",
    "Bread",
    "Rust",
    "Gardening",
    "Retro PCs",
    "Korean cooking",
    "Cycling",
]


def scenario() -> dict:
    p = os.environ.get("FAKE_YTDLP_SCENARIO")
    if p and Path(p).exists():
        return json.loads(Path(p).read_text() or "{}")
    return {}


def fake_id(seed: str) -> str:
    h = hashlib.sha256(seed.encode()).digest()
    return "".join(ALPHABET[b % 64] for b in h[:11])


def fake_channel_id(seed: str) -> str:
    h = hashlib.sha256(("ch" + seed).encode()).digest()
    return "UC" + "".join(ALPHABET[b % 64] for b in h[:22])


def channel_info(sc: dict, channel_id: str) -> dict:
    ch = dict(sc.get("channels", {}).get(channel_id, {}))
    seed = sum(channel_id.encode()) % len(TOPICS)
    ch.setdefault("title", f"{TOPICS[seed]} Channel")
    ch.setdefault("handle", "@" + ch["title"].replace(" ", "").lower())
    ch.setdefault("count", 30)
    return ch


def _registry_path() -> Path:
    base = Path(os.environ.get("FAKE_YTDLP_SAMPLE_DIR", Path.home() / ".cache" / "thytube-fake"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "channel-registry.json"


def registry() -> dict:
    """video id → channel id, so a video's metadata names the channel that listed it."""
    try:
        return json.loads(_registry_path().read_text())
    except (OSError, ValueError):
        return {}


def remember(ids: list[str], channel_id: str) -> None:
    reg = registry()
    reg.update({i: channel_id for i in ids})
    tmp = _registry_path().with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(reg))
    tmp.replace(_registry_path())


def video_info(sc: dict, vid: str) -> dict:
    v = dict(sc.get("videos", {}).get(vid, {}))
    default_duration = int(os.environ.get("FAKE_YTDLP_DURATION", "6"))
    channel_id = v.get("channel_id") or registry().get(vid) or fake_channel_id(vid[:3])
    ch = channel_info(sc, channel_id)
    duration = int(v.get("duration", default_duration))
    title = v.get("title") or f"{ch['title']}: episode {vid[:4]}"
    size = int(v.get("filesize", 40_000 * max(duration, 1)))
    subs = v.get("subtitles", ["en"])
    autos = v.get("auto_captions", ["en"])
    sub_entry = lambda lang: [{"ext": "vtt", "url": f"https://example.invalid/{vid}.{lang}.vtt"}]  # noqa: E731
    auto = {lang: sub_entry(lang) for lang in autos}
    for lang in autos:
        auto[f"{lang}-orig"] = sub_entry(lang)
    for lang in ("de", "fr", "ko", "ja") if autos else ():
        auto.setdefault(lang, sub_entry(lang))  # auto-translations
    return {
        "id": vid,
        "title": title,
        "description": v.get(
            "description", "A fake video.\n\n00:00 Intro\n00:02 Middle\nSee https://example.org"
        ),
        "channel_id": channel_id,
        "channel": ch["title"],
        "uploader": ch["title"],
        "uploader_id": ch["handle"],
        "channel_url": f"https://www.youtube.com/channel/{channel_id}",
        "duration": duration,
        "timestamp": int(v.get("timestamp", 1_700_000_000 + (sum(vid.encode()) * 3600))),
        "upload_date": "20231114",
        "live_status": v.get("live_status", "not_live"),
        "availability": "public",
        "was_live": False,
        "media_type": v.get("media_type", "short" if duration <= 60 and v.get("short") else "video"),
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "chapters": v.get(
            "chapters",
            [
                {"start_time": 0, "end_time": duration / 2, "title": "Intro"},
                {"start_time": duration / 2, "end_time": duration, "title": "Middle"},
            ],
        ),
        "subtitles": {lang: sub_entry(lang) for lang in subs},
        "automatic_captions": auto,
        "height": 1080,
        "width": 1920,
        "vcodec": "avc1.640028",
        "acodec": "mp4a.40.2",
        "format_id": "137+140",
        "requested_formats": [
            {
                "format_id": "137",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
                "width": 1920,
                "filesize": int(size * 0.9),
            },
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "filesize": int(size * 0.1)},
        ],
        "filesize_approx": size,
        "webpage_url": f"https://www.youtube.com/watch?v={vid}",
        "_type": "video",
    }


def flat_entry(sc: dict, vid: str) -> dict:
    info = video_info(sc, vid)
    return {
        "_type": "url",
        "ie_key": "Youtube",
        "id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": info["title"],
        "duration": info["duration"],
        "channel_id": info["channel_id"],
        "channel": info["channel"],
        "thumbnails": [{"url": info["thumbnail"]}],
        "live_status": info["live_status"],
    }


def fail(mode: str) -> None:
    name = MODE_FIXTURE.get(mode)
    text = (FIXTURES / f"{name}.txt").read_text() if name else f"ERROR: fake failure mode {mode}\n"
    sys.stderr.write(text)
    sys.exit(1)


def sample_mp4(duration: int) -> Path:
    cache = Path(os.environ.get("FAKE_YTDLP_SAMPLE_DIR", Path.home() / ".cache" / "thytube-fake"))
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"sample-{duration}s.mp4"
    if not out.exists():
        tmp = out.with_suffix(".tmp.mp4")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=s=320x180:r=15:d={duration}",
                "-f",
                "lavfi",
                "-i",
                f"sine=f=440:d={duration}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "48k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(tmp),
            ],
            check=True,
        )
        tmp.rename(out)
    return out


def sample_jpg() -> Path:
    cache = Path(os.environ.get("FAKE_YTDLP_SAMPLE_DIR", Path.home() / ".cache" / "thytube-fake"))
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / "thumb.jpg"
    if not out.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=480x270",
                "-frames:v",
                "1",
                str(out),
            ],
            check=True,
        )
    return out


def write_vtt(path: Path, lang: str, auto: bool) -> None:
    if auto:
        body = (
            f"WEBVTT\nKind: captions\nLanguage: {lang}\n\n"
            "00:00:00.000 --> 00:00:02.000 align:start position:0%\n"
            "hello<00:00:00.500><c> there</c>\n\n"
            "00:00:02.000 --> 00:00:02.010 align:start position:0%\nhello there\n \n\n"
            "00:00:02.010 --> 00:00:04.000 align:start position:0%\n"
            f"hello there\nthis<00:00:02.500><c> is</c><00:00:03.000><c> {lang}</c>\n"
        )
    else:
        body = f"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nManual subtitle ({lang})\n\n00:00:02.500 --> 00:00:04.000\nSecond line\n"
    path.write_text(body)


def parse(argv: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    with_value = {
        "--js-runtimes",
        "--cache-dir",
        "--color",
        "--retries",
        "--sleep-requests",
        "--cookies",
        "-f",
        "--merge-output-format",
        "--remux-video",
        "--postprocessor-args",
        "--convert-thumbnails",
        "--fragment-retries",
        "--progress-template",
        "-o",
        "--sleep-interval",
        "--max-sleep-interval",
        "--load-info-json",
        "--sub-format",
        "--sub-langs",
        "--playlist-end",
        "--download-sections",
        "--extractor-args",
        "--remote-components",
    }
    opts: dict[str, list[str]] = {}
    pos: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in with_value:
            opts.setdefault(a, []).append(argv[i + 1])
            i += 2
        elif a.startswith("-"):
            opts.setdefault(a, []).append("")
            i += 1
        else:
            pos.append(a)
            i += 1
    return opts, pos


def main() -> None:
    argv = sys.argv[1:]
    if log := os.environ.get("FAKE_YTDLP_LOG"):
        with open(log, "a") as f:
            f.write(json.dumps(argv) + "\n")
    sc = scenario()
    opts, pos = parse(argv)
    if "--version" in opts:
        print(sc.get("version", "2099.01.01-fake"))
        return
    mode = os.environ.get("FAKE_YTDLP_MODE") or sc.get("mode", "ok")
    if mode == "hang":
        time.sleep(3600)
    if mode == "slow":
        time.sleep(float(sc.get("slow_seconds", 3)))
        mode = "ok"
    if mode != "ok":
        fail(mode)

    url = pos[-1] if pos else ""
    info_file = opts.get("--load-info-json", [None])[0]
    if info_file:
        info = json.loads(Path(info_file).read_text())
        v = sc.get("videos", {}).get(info["id"], {})
        dmode = v.get("subs_mode" if "--skip-download" in opts else "download_mode", "ok")
        if dmode == "slow":
            time.sleep(float(sc.get("slow_seconds", 3)))
        elif dmode != "ok":
            fail(dmode)
        return do_download(opts, info)

    if url.startswith("ytsearch"):
        q = url.split(":", 1)[1]
        n = int(url[len("ytsearch") :].split(":")[0] or 1)
        entries = [flat_entry(sc, fake_id(f"{q}-{i}")) for i in range(n)]
        print(json.dumps({"_type": "playlist", "id": q, "title": q, "entries": entries}))
        return
    if "results?search_query=" in url:
        q = url.split("search_query=")[1].split("&")[0]
        entries = []
        for i in range(5):
            cid = fake_channel_id(f"{q}-{i}")
            ch = channel_info(sc, cid)
            entries.append(
                {
                    "_type": "url",
                    "ie_key": "YoutubeTab",
                    "id": cid,
                    "channel_id": cid,
                    "url": f"https://www.youtube.com/channel/{cid}",
                    "title": ch["title"],
                    "channel": ch["title"],
                    "uploader_id": ch["handle"],
                    "thumbnails": [{"url": f"https://yt3.example.invalid/{cid}.jpg"}],
                    "channel_follower_count": 1000 * (i + 1),
                }
            )
        print(json.dumps({"_type": "playlist", "id": q, "entries": entries}))
        return
    if "watch?v=" in url or "youtu.be/" in url or "/shorts/" in url:
        vid = url.split("watch?v=")[-1].split("youtu.be/")[-1].split("/shorts/")[-1][:11]
        vmode = sc.get("videos", {}).get(vid, {}).get("mode", "ok")
        if vmode == "slow":
            time.sleep(float(sc.get("slow_seconds", 3)))
        elif vmode != "ok":
            fail(vmode)
        print(json.dumps(video_info(sc, vid)))
        return
    if "/channel/" in url or "/@" in url:
        if "/@" in url:
            handle = "@" + url.split("/@")[1].split("/")[0]
            cid = next(
                (k for k, v in sc.get("channels", {}).items() if v.get("handle") == handle),
                fake_channel_id(handle),
            )
        else:
            cid = url.split("/channel/")[1].split("/")[0]
        ch = channel_info(sc, cid)
        tab = url.rstrip("/").rsplit("/", 1)[-1]
        limit = int(opts.get("--playlist-end", ["50"])[0])
        ids = ch.get("video_ids") or [fake_id(f"{cid}-{tab}-{i}") for i in range(ch["count"])]
        if tab in ("shorts", "streams") and not ch.get(tab):
            ids = []
        remember(ids[:limit], cid)
        entries = [flat_entry(sc, v) for v in ids[:limit]]
        for e in entries:
            e["channel_id"] = cid
            e["channel"] = ch["title"]
        print(
            json.dumps(
                {
                    "_type": "playlist",
                    "id": cid,
                    "channel_id": cid,
                    "channel": ch["title"],
                    "title": f"{ch['title']} - Videos",
                    "uploader_id": ch["handle"],
                    "thumbnails": [
                        {"id": "avatar_uncropped", "url": f"https://yt3.example.invalid/{cid}.jpg"}
                    ],
                    "entries": entries,
                }
            )
        )
        return
    sys.stderr.write(f"ERROR: [generic] '{url}' is not a valid URL.\n")
    sys.exit(1)


def do_download(opts: dict[str, list[str]], info: dict) -> None:
    outs = opts.get("-o", [])
    main_tpl = next((o for o in outs if o.split(":", 1)[0] not in ("thumbnail", "subtitle")), None)
    thumb_tpl = next((o.split(":", 1)[1] for o in outs if o.startswith("thumbnail:")), None)
    sub_tpl = next((o.split(":", 1)[1] for o in outs if o.startswith("subtitle:")), None)

    if "--skip-download" in opts:
        langs = opts.get("--sub-langs", [""])[0].split(",")
        if sub_tpl:
            for lang in langs:
                if not lang:
                    continue
                manual = lang in info.get("subtitles", {})
                auto = lang in info.get("automatic_captions", {})
                if manual or auto:
                    write_vtt(Path(sub_tpl.replace("%(ext)s", f"{lang}.vtt")), lang, auto=not manual)
        return

    if "--download-sections" in opts:
        dest = Path(main_tpl.replace("%(ext)s", "mp4"))
        shutil.copy(sample_mp4(2), dest)
        return

    duration = int(info.get("duration") or 6)
    src = sample_mp4(duration)
    total = int(info.get("filesize_approx") or src.stat().st_size)
    for frac in (0.1, 0.5, 1.0):
        print(f"[thytube-progress] {int(total * frac)} {total} NA 1000000.0 1 137", flush=True)
        time.sleep(0.05)
    dest = Path(main_tpl.replace("%(ext)s", "mp4"))
    shutil.copy(src, dest)
    if thumb_tpl and "--write-thumbnail" in opts:
        shutil.copy(sample_jpg(), Path(thumb_tpl.replace("%(ext)s", "jpg")))


if __name__ == "__main__":
    main()
