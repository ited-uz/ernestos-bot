#!/usr/bin/env sh
# ErnestOS on this computer (macOS / Linux): ./start-local.sh
cd "$(dirname "$0")" || exit 1
exec python3 run_local.py "$@"
