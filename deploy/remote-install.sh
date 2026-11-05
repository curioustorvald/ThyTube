#!/usr/bin/env bash
# Server half of deploy.sh (runs as the app user after the rsync). Also usable by hand:
#   DEPLOY_ORIGIN=/path/to/origin deploy/remote-install.sh
set -euo pipefail
O="${DEPLOY_ORIGIN:?set DEPLOY_ORIGIN}"
DEPLOY_PYTHON="${DEPLOY_PYTHON:-python3.13}"
RESTART="${RESTART:-1}"
WEB_ONLY="${WEB_ONLY:-0}"
export THYTUBE_CONFIG="$O/config.toml"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

# A venv is not relocatable: the scripts in venv/bin carry absolute #! paths, so a venv that was
# created elsewhere and moved/copied here looks fine but fails to run (`required file not found`).
venv_ok() {
  [ -x "$O/venv/bin/python" ] || return 1
  "$O/venv/bin/python" -c 'import sys' 2>/dev/null || return 1
  "$O/venv/bin/pip" --version 2>/dev/null | grep -qF "$O/venv/"
}
if ! venv_ok; then
  if [ -e "$O/venv" ]; then
    echo "    $O/venv is broken or was moved from another path; rebuilding it"
  else
    echo "    creating $O/venv"
  fi
  "$DEPLOY_PYTHON" -m venv --clear "$O/venv"
fi
PY="$O/venv/bin/python"
"$PY" -m pip install -q --upgrade pip
# Editable install: the package finds app/web next to app/src.
"$PY" -m pip install -q -e "$O/app"
TT=("$PY" -m thytube.cli)

if [ ! -f "$THYTUBE_CONFIG" ]; then
  cp "$O/app/config.example.toml" "$THYTUBE_CONFIG"
  echo "!!  created $THYTUBE_CONFIG from the example — edit paths/port, then run deploy again"
  exit 1
fi
if ! "$O/ytdlp/current/bin/yt-dlp" --version >/dev/null 2>&1; then
  # Missing (first deploy) or not runnable (e.g. the origin was moved): install a fresh one.
  echo "    installing yt-dlp"
  "${TT[@]}" update-ytdlp || true
fi
"${TT[@]}" migrate
"${TT[@]}" doctor || echo "!!  doctor reported problems (see above)"

if [ "$RESTART" = 1 ]; then
  roles="serve"; [ "$WEB_ONLY" = 1 ] || roles="serve worker"
  for r in $roles; do
    # SIGTERM: systemd (Restart=always) or the cron watchdog brings it back with the new code.
    if pkill -TERM -u "$(id -u)" -f "$O/venv/bin/thytube $r"; then echo "    restarted $r"; else echo "    $r was not running"; fi
  done
  if ! systemctl is-enabled --quiet thytube-web 2>/dev/null; then
    sleep 3
    "$O/app/deploy/watchdog.sh" && echo "    started via watchdog"
  fi
fi
