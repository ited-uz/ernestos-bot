#!/bin/bash
# ErnestOS web — o'rnatish (macOS; Linux'da ham ishlaydi). Finder'da ikki marta bosing.
# Dasturni ~/Documents/PROJECTS/ernestOS web ga ko'chiradi va ishga tushiradi.
# Ma'lumotlar (data/), .venv va .env.local ustidan hech qachon yozilmaydi.
cd "$(dirname "$0")" || exit 1
SRC="$(pwd -P)"
DOCS="$HOME/Documents"
PROJECTS="$DOCS/PROJECTS"
[ ! -d "$PROJECTS" ] && [ -d "$DOCS/PROFECTS" ] && PROJECTS="$DOCS/PROFECTS"
DEST="$PROJECTS/ernestOS web"
echo ""
echo "  ErnestOS web"
echo "  Papka: $DEST"
echo ""
if [ "$SRC" != "$(cd "$DEST" 2>/dev/null && pwd -P)" ]; then
  mkdir -p "$DEST"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude ".venv" --exclude "data" --exclude ".env.local" --exclude "__pycache__" "$SRC"/ "$DEST"/
  else
    # No rsync (some Linux): the downloaded folder holds no data, .venv or
    # .env.local, so a plain copy cannot touch those in the destination.
    cp -R "$SRC"/. "$DEST"/
  fi || { echo "Fayllarni ko'chirib bo'lmadi."; read -r -p "Enter..." _; exit 1; }
  echo "  • Fayllar ko'chirildi"
fi
cd "$DEST" || exit 1
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 yoki yangisi kerak: https://www.python.org/downloads/ — o'rnating va shu faylni qayta oching."
  command -v open >/dev/null 2>&1 && open "https://www.python.org/downloads/macos/"
  read -r -p "Enter..." _
  exit 1
fi
echo "  • $($PY --version)"
exec "$PY" run_local.py "$@"
