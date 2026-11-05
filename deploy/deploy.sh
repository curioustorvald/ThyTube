#!/usr/bin/env bash
# Deploy from the dev machine to the server, as the app user (no root needed).
#
# Settings come from deploy/deploy.env (git-ignored) or the environment:
#   DEPLOY_SSH      user@host
#   DEPLOY_PORT     SSH port (default 22)
#   DEPLOY_ORIGIN   app home on the server (holds app/, venv/, ytdlp/, data/, config.toml)
#   DEPLOY_PYTHON   python on the server (default python3.13)
#
# Usage: deploy/deploy.sh [--no-restart] [--web-only]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f deploy/deploy.env ] && . deploy/deploy.env
: "${DEPLOY_SSH:?set DEPLOY_SSH (see deploy/deploy.env.example)}"
: "${DEPLOY_ORIGIN:?set DEPLOY_ORIGIN}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_PYTHON="${DEPLOY_PYTHON:-python3.13}"
RESTART=1; WEB_ONLY=0
for a in "$@"; do
  case "$a" in
    --no-restart) RESTART=0 ;;
    --web-only) WEB_ONLY=1 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
SSH=(ssh -p "$DEPLOY_PORT" "$DEPLOY_SSH")

echo "==> rsync to $DEPLOY_SSH:$DEPLOY_ORIGIN/app"
"${SSH[@]}" "mkdir -p '$DEPLOY_ORIGIN/app' '$DEPLOY_ORIGIN/logs' '$DEPLOY_ORIGIN/run' '$DEPLOY_ORIGIN/data'"
rsync -az --delete -e "ssh -p $DEPLOY_PORT" \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '*.egg-info' \
  src web deploy pyproject.toml README.md config.example.toml \
  "$DEPLOY_SSH:$DEPLOY_ORIGIN/app/"

echo "==> remote install"
"${SSH[@]}" DEPLOY_ORIGIN="$DEPLOY_ORIGIN" DEPLOY_PYTHON="$DEPLOY_PYTHON" RESTART="$RESTART" WEB_ONLY="$WEB_ONLY" bash -s <<'REMOTE'
set -euo pipefail
O="$DEPLOY_ORIGIN"
export THYTUBE_CONFIG="$O/config.toml"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
if [ ! -x "$O/venv/bin/python" ]; then
  echo "    creating $O/venv"
  "$DEPLOY_PYTHON" -m venv "$O/venv"
  "$O/venv/bin/pip" install -q --upgrade pip
fi
# Editable install: the package finds app/web next to app/src.
"$O/venv/bin/pip" install -q -e "$O/app"
if [ ! -f "$THYTUBE_CONFIG" ]; then
  cp "$O/app/config.example.toml" "$THYTUBE_CONFIG"
  echo "!!  created $THYTUBE_CONFIG from the example — edit paths/port, then run deploy again"
  exit 1
fi
if [ ! -e "$O/ytdlp/current" ]; then
  echo "    first yt-dlp install"
  "$O/venv/bin/thytube" update-ytdlp || true
fi
"$O/venv/bin/thytube" migrate
"$O/venv/bin/thytube" doctor || echo "!!  doctor reported problems (see above)"

if [ "$RESTART" = 1 ]; then
  roles="serve"; [ "$WEB_ONLY" = 1 ] || roles="serve worker"
  for r in $roles; do
    # SIGTERM: systemd (Restart=always) or the cron watchdog brings it back with the new code.
    pkill -TERM -u "$(id -u)" -f "$O/venv/bin/thytube $r" && echo "    restarted $r" || echo "    $r was not running"
  done
  if ! systemctl is-enabled --quiet thytube-web 2>/dev/null; then
    sleep 3
    "$O/app/deploy/watchdog.sh" && echo "    started via watchdog"
  fi
fi
REMOTE
echo "==> done"
