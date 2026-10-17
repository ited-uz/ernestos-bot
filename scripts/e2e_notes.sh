#!/usr/bin/env bash
# Browser checks of Notes against a real server (headless Chromium).
#   scripts/e2e_notes.sh      # needs .venv (or python) and Node with playwright
# Screenshots: tests/e2e/out/notes-*.png
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${E2E_OUT:-$PWD/tests/e2e/out}"; mkdir -p "$OUT"
PY="${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python )}"
export BOT_TOKEN="123456:E2E-TOKEN" ENVIRONMENT=test REQUIRED_CHANNEL_ID= ADMIN_LOG_CHANNEL_ID=
export DATABASE_URL="sqlite:///$OUT/e2e-notes.db" E2E_OUT="$OUT" PORT="${E2E_PORT:-8767}"
export E2E_BASE="http://localhost:$PORT"
rm -f "$OUT/e2e-notes.db"
"$PY" tests/e2e/seed.py "$OUT/initdata.txt"
# A small Obsidian vault, synthetic, to import through the UI.
"$PY" - "$OUT/vault.zip" <<'PY'
import sys, zipfile
png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
with zipfile.ZipFile(sys.argv[1], "w") as z:
    z.writestr("Vault/.obsidian/app.json", "{}")
    z.writestr("Vault/AI/log.md", "agent log")
    z.writestr("Vault/University/Marketing 101.md",
               "---\ntags: [marketing]\n---\n## 4P\n- product\n\n```dataviewjs\ndv.list()\n```\n![[board.png]]\n")
    z.writestr("Vault/University/board.png", png)
    z.writestr("Vault/Ideas/Positioning.md", "Own one word in the mind. See [[Marketing 101]].")
PY
"$PY" -m uvicorn app:app --port "$PORT" > "$OUT/server-notes.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do curl -sf "$E2E_BASE/api/version" >/dev/null && break; sleep 0.25; done
NODE_PATH="${NODE_PATH:-$(npm root -g)}" node tests/e2e/notes.cjs
