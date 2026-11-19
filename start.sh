#!/usr/bin/env sh
# Start the Chat Media dashboard on macOS / Linux, then open http://localhost:8000
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"   # HOST=0.0.0.0 to reach it from other devices (passcode protected)
# Web workers, and how many requests each serves before it is replaced with a fresh one.
export WEB_WORKERS="${WEB_WORKERS:-4}" MAX_REQUESTS="${MAX_REQUESTS:-2000}" MAX_REQUESTS_JITTER="${MAX_REQUESTS_JITTER:-200}"
# The background engine (Instagram work) is replaced after this many tasks.
export ENGINE_MAX_JOBS="${ENGINE_MAX_JOBS:-50}"
export HOST PORT

if [ ! -x .venv/bin/python ]; then
  echo "Setting up Python for the first time..."
  python3 -m venv .venv
fi
echo "Checking Python packages..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r dashboard/backend/requirements.txt

if [ ! -f dashboard/frontend/dist/index.html ]; then
  command -v npm >/dev/null 2>&1 || { echo "Install Node.js 20+ (https://nodejs.org) or use Docker."; exit 1; }
  echo "Building the web app (first time only)..."
  (cd dashboard/frontend && npm ci --no-audit --no-fund && npm run build)
fi

echo
echo "  Chat Media is running at http://localhost:$PORT  (Ctrl+C to stop)"
echo
( sleep 3; (command -v open >/dev/null && open "http://localhost:$PORT") || (command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT") || true ) >/dev/null 2>&1 &
PYTHONPATH="$(pwd)/dashboard/backend" exec .venv/bin/python -m app.serve
