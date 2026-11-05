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
"${SSH[@]}" DEPLOY_ORIGIN="$DEPLOY_ORIGIN" DEPLOY_PYTHON="$DEPLOY_PYTHON" RESTART="$RESTART" WEB_ONLY="$WEB_ONLY" \
  bash "$DEPLOY_ORIGIN/app/deploy/remote-install.sh"
echo "==> done"
