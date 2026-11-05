<p align="center"><img src="web/icons/icon-192.png" width="96" height="96" alt=""></p>

<h1 align="center">ThyTube</h1>

<p align="center"><em>thy</em> (determiner, archaic): of or belonging to thee.<br>
Not <em>your</em> tube. Not <em>their</em> tube. <strong>Thy</strong> tube.</p>

---

ThyTube is a private YouTube caching server with a frontend of its own, for thy home network.
Thou subscribest to channels through **filters** (title patterns, duration, Shorts, streams); the
uploads that pass are downloaded into a pool of fixed size and served to thy phones, tablets and
computers. When YouTube breaks yt-dlp (and it will), ThyTube notices, stops hammering, updates
yt-dlp and tells thee plainly what is going on.

No recommendations, no adverts, no autoplaying rabbit holes. Only the videos thou hast asked for,
on thine own disk.

## What it doth

- **Library**: what is cached, what thou wast halfway through, filters per channel. No infinite
  scroll, just a "Load more" button.
- **Player**: remembers where each profile stopped, shows chapters on the seek bar, and draws its
  own subtitles (YouTube's auto-captions, cleaned of their stutter). Double-tap to skip, keyboard
  shortcuts on the desktop. When a video ends, it picks another from the same channel, with the
  ones thou hast watched least twice as likely.
- **Search**: searches YouTube through yt-dlp on the server, and takes pasted links too. Every
  result offers *Cache this video* and *Cache this creator…*. The latter opens the filter dialog
  with a live preview of which recent uploads would be caught.
- **A pool that keeps its promise**: it never exceeds its budget. The least re-watched videos are
  removed first; fresh, unwatched downloads get a grace period; pinned videos stay; nothing is
  removed while it is playing.
- **Health**: an `ok → degraded → blocked` circuit breaker with a canary probe, and a daily yt-dlp
  update that is tested before it is trusted and rolled back if it is worse.
- **Profiles** without passwords ("Who's watching?"). It runs on thy LAN only, so all who reach it
  are trusted.
- **Installable**: a Progressive Web App that sits on thy home screen like any other app (see
  [HTTPS](#https-for-the-installable-app)).

## What thou needest

- Linux, Python ≥ 3.13, ffmpeg and ffprobe
- A JavaScript runtime for yt-dlp's YouTube challenges (Node by default; see `ytdlp.js_runtime`)
- Apache, or another web server, to serve the frontend and the media and to proxy `/api`
- yt-dlp is **not** a Python dependency: `thytube update-ytdlp` installs it into a venv of its
  own, and it is only ever run as a separate process

## How it is laid out

```
<origin>/                 the app's home (a fast disk)
  app/  venv/             the code and its venv
  ytdlp/venv-…/ current   installed yt-dlp versions; `current` is an atomic symlink
  data/thytube.sqlite3    the database (WAL)
  config.toml  logs/  run/
<pool>/                   the media (a big disk); only the worker writes here
  media/<id[0:2]>/<id>/video.mp4, thumb.jpg, subs/<lang>.<manual|auto>.vtt
  channels/<channel id>/avatar.jpg
  .partial/               downloads in progress (same filesystem, so finishing is an atomic rename)
```

Two processes run from one codebase: `thytube serve` (web and API) and `thytube worker` (all bulk
traffic to YouTube and every write to the pool). They speak to each other only through the database.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest              # never touches YouTube: the tests use tests/fake_ytdlp.py
.venv/bin/ruff check src tests && .venv/bin/ruff format src tests
```

To run it locally against the fake yt-dlp, make thyself a `config.dev.toml` (git-ignored):

```toml
[paths]
origin = "./.dev"       # relative paths are resolved against this file's directory
pool = "./pool"
[server]
port = 8471
serve_media = true      # development only: FastAPI serves /media and the frontend
serve_static = true
[pool]
max_bytes = 30_000_000  # tiny, so that eviction actually happens
min_free_bytes = 0
[ytdlp]
binary = "tests/fake_ytdlp.py"
sleep_requests = 0.0
sleep_interval = 0
max_sleep_interval = 0
[discovery]
rss_url_template = ""   # no RSS: use (fake) channel listings
```

```sh
export THYTUBE_CONFIG=config.dev.toml
.venv/bin/thytube serve &      # http://localhost:8471/
.venv/bin/thytube worker &
```

Start the worker with `FAKE_YTDLP_MODE=botcheck` (or `403`, `429`, `private`, …) to watch the
health machinery earn its keep; the top of `tests/fake_ytdlp.py` lists every mode.

The frontend has no build step: plain ES modules and a vendored Preact + htm (`web/vendor/`).

**House style:** ThyTube addresses its user in the singular. All user-facing text says
*thou / thee / thy / thine*, never *you / your*. Code and comments stay in ordinary English.

## Deployment

Everything particular to thy server lives in `config.toml` and `deploy/deploy.env`, both
git-ignored. Every key is documented in `config.example.toml`; use absolute paths.

1. **As the app user, from thy development machine**: copy `deploy/deploy.env.example` to
   `deploy/deploy.env`, fill it in, and run `deploy/deploy.sh`. It copies the code over, builds
   (or rebuilds) the venv, installs yt-dlp the first time, migrates the database and runs
   `thytube doctor`. On the very first run it writes `config.toml` from the example and stops,
   so that thou mayest edit it.
2. **As root, once**:
   - The web server: fill in one of the Apache templates and copy it to `/etc/apache2/vhosts.d/`.
     - `deploy/apache/thytube-port.conf` gives ThyTube its own LAN port and needs no DNS. Open
       that port in the firewall, for the LAN zone only.
     - `deploy/apache/thytube-hostname.conf` is a name-based vhost on port 80. It needs a local
       DNS or hosts entry on every device.

     Both admit only private address ranges (`Require ip`), serve `/media/` straight from the
     pool with Range support and immutable cache headers, and proxy `/api/` to uvicorn.
   - The processes: fill in `@ORIGIN@` in `deploy/systemd/*.service`, copy them to
     `/etc/systemd/system/`, and run `systemctl enable --now thytube-web thytube-worker`.
     If thou wouldst rather not use systemd, the `@reboot` and `*/5` lines in
     `deploy/crontab.txt` keep both running without root. Use one or the other, never both.
3. **The app user's crontab**: install `deploy/crontab.txt` for the daily `thytube update-ytdlp`.

Restarting the web process never interrupts a download. The worker leaves gracefully on SIGTERM:
it abandons the current download, tidies up after itself, and puts the video back in the queue.

### HTTPS, for the installable app

Browsers only offer *Install app* (on Android and desktop) and only start the app offline when it
is reached over HTTPS. On plain HTTP everything else works, and iOS's *Add to Home Screen* still
gives thee a full-screen app.

For HTTPS on a LAN, make thyself a little certificate authority with
[mkcert](https://github.com/FiloSottile/mkcert):

```sh
mkcert -install                        # creates thy local CA
mkcert <server-ip> <server-hostname>   # issues a certificate for the server
```

Then, as root, copy the certificate and key to the server, enable `mod_ssl`, and uncomment the
`SSL…` lines in the vhost (or copy the hostname vhost to `*:443`). Finally, let each device trust
thy CA (`rootCA.pem`, found with `mkcert -CAROOT`):

- **iPhone/iPad**: send the file to the device and install the profile, then turn it on under
  Settings › General › About › Certificate Trust Settings.
- **Android**: Settings › Security › Encryption & credentials › Install a certificate › CA certificate.

### Commands

```
thytube serve | worker | migrate | update-ytdlp [--force] | rollback-ytdlp | probe | reconcile | doctor
```

## When YouTube blocketh thee

The Status page explains the present state in plain words. What thou canst do, roughly in order:

1. **Wait.** The canary probe backs off from 15 minutes to 6 hours, and the queue resumes the
   moment it passes.
2. **Update.** Press *Update yt-dlp now* on the Status page, or set `ytdlp.channel = "nightly"`;
   nightly builds often mend YouTube breakage sooner.
3. **Slow down.** Raise `sleep_requests`, `sleep_interval` and `max_sleep_interval`.
4. **Bring a token.** Install a PO-token provider plugin into the yt-dlp venvs with
   `ytdlp.extra_pip_packages`.
5. **Last resort, off by default.** Point `ytdlp.cookies` at a `cookies.txt` exported from a
   *throwaway* Google account. Google may flag or ban the account those cookies belong to. Never,
   ever use thine own.

## Notes

- Media is stored as H.264/AAC MP4 (≤ 1080p by default, `faststart`), so it plays on iOS
  without transcoding. Should only another codec be on offer, the video is kept and wears a
  warning badge.
- Subtitles are fetched in a separate step that may fail without harming the video (YouTube
  answers subtitle requests with 429s rather often). They are cleaned and shown in ThyTube's own
  overlay, with native tracks attached too for the iPhone's fullscreen player. By default only
  real subtitles and auto-captions in the video's own language are fetched, no machine
  translations.
- A video that was removed to make room is never downloaded again by a subscription; only thou
  canst bring it back, with "Cache again".

## Why "thy"?

Because it is **thine**. Thy server, thy disk, thy subscriptions, thy business. Nobody else's
algorithm decides what thou watchest next.
