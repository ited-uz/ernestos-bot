#!/usr/bin/env bash
# Browser checks of the Mini App against a real server (headless Chromium).
#   scripts/e2e.sh            # needs .venv (or python) and Node with playwright
# Screenshots: tests/e2e/out/*.png
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${E2E_OUT:-$PWD/tests/e2e/out}"; mkdir -p "$OUT"
PY="${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python )}"
export BOT_TOKEN="123456:E2E-TOKEN" ENVIRONMENT=test REQUIRED_CHANNEL_ID= ADMIN_LOG_CHANNEL_ID=
export DATABASE_URL="sqlite:///$OUT/e2e.db" E2E_OUT="$OUT" PORT="${E2E_PORT:-8765}"
export E2E_BASE="http://localhost:$PORT"
rm -f "$OUT/e2e.db"
"$PY" tests/e2e/seed.py "$OUT/initdata.txt"
"$PY" -m uvicorn app:app --port "$PORT" > "$OUT/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do curl -sf "$E2E_BASE/api/version" >/dev/null && break; sleep 0.25; done
NODE_PATH="${NODE_PATH:-$(npm root -g)}" node tests/e2e/browser.cjs
