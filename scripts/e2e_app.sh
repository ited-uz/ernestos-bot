#!/usr/bin/env bash
# Browser checks of the phone app's web layer (mobile/www) against a real
# server: sign-in code, Mini App through native.js, session end.
#   scripts/e2e_app.sh     # needs .venv (or python), Node with playwright
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${E2E_OUT:-$PWD/tests/e2e/out}"; mkdir -p "$OUT"
PY="${PYTHON:-$( [ -x .venv/bin/python ] && echo "$PWD/.venv/bin/python" || echo python )}"
PORT="${E2E_PORT:-8765}"; SHELL_PORT="${E2E_SHELL_PORT:-8766}"
export BOT_TOKEN="123456:E2E-TOKEN" ENVIRONMENT=test REQUIRED_CHANNEL_ID= ADMIN_LOG_CHANNEL_ID=
export DATABASE_URL="sqlite:///$OUT/e2e-app.db" E2E_OUT="$OUT" PY
export APP_ORIGINS="http://localhost:$SHELL_PORT"
export APP_SHELL_BASE="http://localhost:$SHELL_PORT"
rm -f "$OUT/e2e-app.db"
"$PY" tests/e2e/seed.py "$OUT/initdata.txt"
ERNEST_ALLOW_HTTP=1 ERNEST_API_URL="http://127.0.0.1:$PORT" node mobile/scripts/build-www.mjs
"$PY" -m uvicorn app:app --port "$PORT" > "$OUT/server-app.log" 2>&1 &
SERVER=$!
"$PY" -m http.server "$SHELL_PORT" --directory mobile/www > "$OUT/shell.log" 2>&1 &
STATIC=$!
trap 'kill $SERVER $STATIC 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/api/version" >/dev/null && break; sleep 0.25; done
NODE_PATH="${NODE_PATH:-$(npm root -g)}" node tests/e2e/app_shell.cjs
