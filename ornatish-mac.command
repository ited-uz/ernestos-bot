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
find_python() {
  # PATH first, then where Homebrew and the python.org installer put it
  # (a Finder-opened window may not have those on PATH yet).
  for c in python3.13 python3.12 python3.11 python3 \
           /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
           /Library/Frameworks/Python.framework/Versions/3.1[1-9]/bin/python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$c"; return 0
    fi
  done
  return 1
}
PY=""
if ! find_python; then
  # A Mac comes with Python 3.9 at most: install a newer one, once.
  echo "  • Python 3.11+ topilmadi — o'rnatilmoqda…"
  if command -v brew >/dev/null 2>&1; then
    brew install python@3.12 && find_python
  elif [ "$(uname)" = "Darwin" ]; then
    PKG="${TMPDIR:-/tmp}/python-3.12.10-macos11.pkg"
    if curl -fL --progress-bar -o "$PKG" "https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg"; then
      echo "  • Python o'rnatuvchisi ochildi: Continue → Install, tugagach Close."
      open -W "$PKG"
      find_python
    fi
  fi
fi
if [ -z "$PY" ]; then
  echo "Python 3.11 yoki yangisi kerak: https://www.python.org/downloads/ — o'rnating va shu faylni qayta oching."
  command -v open >/dev/null 2>&1 && open "https://www.python.org/downloads/macos/"
  read -r -p "Enter..." _
  exit 1
fi
echo "  • $($PY --version)"
# The next time: a double-click on the desktop. Written here, on this Mac,
# so it carries no download quarantine and opens without a Gatekeeper prompt.
if [ "$(uname)" = "Darwin" ] && [ -d "$HOME/Desktop" ] && [ ! -e "$HOME/Desktop/ErnestOS.command" ]; then
  if { printf '#!/bin/bash\n# ErnestOS — shu Mac’da. Ikki marta bosing; oynani yopmang.\n'
       printf 'exec /bin/bash %q\n' "$DEST/ornatish-mac.command"; } > "$HOME/Desktop/ErnestOS.command" 2>/dev/null \
     && chmod +x "$HOME/Desktop/ErnestOS.command"; then
    echo "  • Ish stolida 'ErnestOS' — keyingi safar shuni ikki marta bosing"
  fi
fi
exec "$PY" run_local.py "$@"
