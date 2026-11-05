#!/bin/sh
# No-root fallback for systemd: (re)start the web and worker processes if they aren't running.
# Run from the app user's crontab (@reboot and */5). Don't use it together with the systemd units.
set -eu
APP="$(cd "$(dirname "$0")/.." && pwd)"
ORIGIN="$(dirname "$APP")"
export THYTUBE_CONFIG="${THYTUBE_CONFIG:-$ORIGIN/config.toml}"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
mkdir -p "$ORIGIN/run" "$ORIGIN/logs"
# Never compete with the systemd units: two web processes fight over the port.
if systemctl is-enabled --quiet thytube-web 2>/dev/null || systemctl is-enabled --quiet thytube-worker 2>/dev/null; then
  exit 0
fi

rotate() { # keep logs bounded: one 50 MB generation
  if [ -f "$1" ] && [ "$(stat -c %s "$1")" -gt 52428800 ]; then mv -f "$1" "$1.1"; fi
}

start() { # $1 = role (web|worker), $2 = thytube sub-command
  rotate "$ORIGIN/logs/$1.log"
  # flock -n holds the lock for as long as the process lives; if it's held, it's already running.
  nohup flock -n "$ORIGIN/run/$1.flock" "$ORIGIN/venv/bin/thytube" "$2" >>"$ORIGIN/logs/$1.log" 2>&1 &
}

start web serve
start worker worker
