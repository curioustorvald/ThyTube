# ThyTube

*thy* (determiner): that which belongs to thee.

A private YouTube caching server with its own frontend, meant for a home LAN. You subscribe to
channels through **filters** (title regexes, duration, Shorts, streams). Matching uploads are
downloaded into a size-capped pool and served to phones, tablets and PCs. When YouTube breaks
yt-dlp (it will), the server notices, stops hammering, updates yt-dlp and shows what's going on.

- **Library**: what's cached, continue watching, per-channel filters. No infinite scroll.
- **Player**: resume per profile, chapters on the seek bar, our own subtitle overlay (cleaned
  auto-captions), double-tap seek, keyboard shortcuts, and a next-video pick that shuffles within
  the channel (less-watched videos are twice as likely).
- **Search**: searches YouTube through yt-dlp on the server, accepts pasted links, and has
  *Cache this video* and *Cache this creator…* (the filter dialog, with a live preview of which
  recent uploads would match).
- **Pool**: never exceeds its budget. Least re-watched videos go first, fresh unwatched downloads
  get a grace period, and pinned videos stay.
- **Health**: `ok → degraded → blocked` circuit breaker with a canary probe, plus a daily yt-dlp
  update that is canary-tested and rolls back on its own.
- **Profiles** without passwords ("who's watching?"). LAN only, so everyone on it is trusted.

## Requirements

- Linux, Python ≥ 3.13, ffmpeg + ffprobe, and a JavaScript runtime for yt-dlp's YouTube
  challenges (Node is used by default; see `ytdlp.js_runtime`)
- Apache (or another web server) to serve the frontend and the media files and to proxy `/api`
- yt-dlp is **not** a Python dependency: `thytube update-ytdlp` installs it into its own
  versioned venv, and it is always run as a subprocess

## Layout

```
<origin>/                 app home (fast disk)
  app/  venv/             code + its venv
  ytdlp/venv-…/ current   yt-dlp versions; `current` is an atomic symlink
  data/thytube.sqlite3    database (WAL)
  config.toml  logs/  run/
<pool>/                   media (big disk); only the worker writes here
  media/<id[0:2]>/<id>/video.mp4, thumb.jpg, subs/<lang>.<manual|auto>.vtt
  channels/<channel id>/avatar.jpg
  .partial/               in-progress downloads (same filesystem → atomic rename)
```

Two processes run from one codebase: `thytube serve` (web/API) and `thytube worker` (all bulk
YouTube traffic and all pool writes). They talk only through the database.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest              # never touches YouTube: tests use tests/fake_ytdlp.py
.venv/bin/ruff check src tests && .venv/bin/ruff format src tests
```

To run locally against the fake yt-dlp, create `config.dev.toml` (git-ignored):

```toml
[paths]
origin = "./.dev"
pool = "./pool"
[server]
port = 8471
serve_media = true      # dev only: FastAPI serves /media and the frontend
serve_static = true
[pool]
max_bytes = 30_000_000  # tiny, to exercise eviction
min_free_bytes = 0
[ytdlp]
binary = "tests/fake_ytdlp.py"
sleep_requests = 0.0
sleep_interval = 0
max_sleep_interval = 0
[discovery]
rss_url_template = ""   # no RSS: use (fake) flat listings
```

```sh
export THYTUBE_CONFIG=config.dev.toml
.venv/bin/thytube serve &      # http://localhost:8471/
.venv/bin/thytube worker &
```

Set `FAKE_YTDLP_MODE=botcheck` (or `403`, `429`, `private`, …) on the worker to watch the
health machinery react. See the top of `tests/fake_ytdlp.py` for the full list.

The frontend has no build step: ES modules plus a vendored Preact + htm (`web/vendor/`).

## Deployment

Everything deployment-specific lives in `config.toml` and `deploy/deploy.env`, both git-ignored.
Every key is documented in `config.example.toml`.

1. **As the app user, from your dev machine**: copy `deploy/deploy.env.example` to
   `deploy/deploy.env`, fill it in, and run `deploy/deploy.sh`. It rsyncs the code, creates the
   venv, installs yt-dlp on first run, migrates the database and runs `thytube doctor`. On the
   very first run it writes `config.toml` from the example and stops so you can edit it.
2. **As root, once**:
   - Web server: fill in one of the Apache templates and copy it to `/etc/apache2/vhosts.d/`:
     - `deploy/apache/thytube-port.conf`: its own LAN port, no DNS needed. Open that port in
       the firewall for the LAN zone only.
     - `deploy/apache/thytube-hostname.conf`: a name-based vhost on port 80. Needs a local DNS
       or hosts entry on every device.

     Both restrict everything to private address ranges (`Require ip`), serve `/media/`
     straight from the pool with Range support and immutable cache headers, and proxy `/api/`
     to uvicorn.
   - Processes: fill in `@ORIGIN@` in `deploy/systemd/*.service`, then copy them to
     `/etc/systemd/system/` and run `systemctl enable --now thytube-web thytube-worker`.
     If you'd rather not use systemd, the `@reboot`/`*/5` watchdog lines in
     `deploy/crontab.txt` keep both running without root.
3. **App user's crontab**: install `deploy/crontab.txt` (daily `thytube update-ytdlp`).

Restarting the web process never interrupts downloads. The worker shuts down gracefully on
SIGTERM: it abandons the current download, cleans up, and re-queues it.

### Commands

```
thytube serve | worker | migrate | update-ytdlp [--force] | rollback-ytdlp | probe | reconcile | doctor
```

## When YouTube blocks downloads

The Status page explains the current state in plain language. Things you can do, roughly in
order:

1. Wait. The canary probe backs off from 15 minutes up to 6 hours and resumes the queue as soon
   as it passes.
2. *Update yt-dlp now* (Status page), or set `ytdlp.channel = "nightly"`. Nightly builds often fix
   YouTube breakage sooner.
3. Slow down: raise `sleep_requests`, `sleep_interval` and `max_sleep_interval`.
4. Install a PO-token provider plugin into the yt-dlp venvs via `ytdlp.extra_pip_packages`.
5. **Last resort, off by default**: point `ytdlp.cookies` at a `cookies.txt` exported from a
   *throwaway* Google account. Google may flag or ban the account that the cookies belong to.
   Never use your main account.

## Notes

- Media is H.264/AAC MP4, ≤ 1080p by default, `faststart`, so it plays on iOS without
  transcoding. If only another codec is available, the video is kept and gets a warning badge.
- Subtitles are fetched in a separate, non-fatal step (429s are common), cleaned (YouTube's
  rolling auto-caption cues are merged) and shown in our own overlay. Native tracks are attached
  too, for iPhone's fullscreen player. By default only manual subtitles and auto-captions in the
  video's own language are fetched, not machine translations.
- Evicted videos are never re-downloaded by subscription polling, only by hand
  ("cache again").
