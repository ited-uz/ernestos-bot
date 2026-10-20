#!/usr/bin/env bash
# Browser checks of the web app (/app/) against a real server, in headless
# Chromium on a laptop and a phone: the way in from a plain browser, email
# sign-up, the bot code, every screen at both sizes, back, downloads,
# install files, offline start, sign-out.
#   scripts/e2e_web.sh     # needs .venv (or python), Node with playwright
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${E2E_OUT:-$PWD/tests/e2e/out}"; mkdir -p "$OUT"
PY="${PYTHON:-$( [ -x .venv/bin/python ] && echo "$PWD/.venv/bin/python" || echo python )}"
PORT="${E2E_PORT:-8768}"
export BOT_TOKEN="123456:E2E-TOKEN" ENVIRONMENT=test REQUIRED_CHANNEL_ID= ADMIN_LOG_CHANNEL_ID=
export DATABASE_URL="sqlite:///$OUT/e2e-web.db" E2E_OUT="$OUT" PY
export EMAIL_PROVIDER=file EMAIL_OUTBOX_DIR="$OUT/mail-web"
export GOOGLE_CLIENT_ID=e2e-web.apps.googleusercontent.com GOOGLE_CLIENT_SECRET=e2e-not-a-secret
export PUBLIC_URL="http://127.0.0.1:$PORT" WEB_BASE="http://127.0.0.1:$PORT"
rm -rf "$OUT/e2e-web.db" "$OUT/mail-web"
"$PY" tests/e2e/seed.py "$OUT/initdata-web.txt"
"$PY" -m uvicorn app:app --port "$PORT" > "$OUT/server-web.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/api/version" >/dev/null && break; sleep 0.25; done
NODE_PATH="${NODE_PATH:-$(npm root -g)}" node tests/e2e/web.cjs
