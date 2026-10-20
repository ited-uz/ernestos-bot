"""The local web app as one ZIP: what a person downloads to run ErnestOS on
their own computer (Documents/PROJECTS/ernestOS web).

    python scripts/package_local.py [out.zip]

Only files Git knows (tracked, or new and not ignored) go in, so no .env,
database, mailbox or virtual environment ever does; the phone app's build,
the tests and CI are left out. Everything sits in an "ernestOS web/" folder
with BOSHLASH.txt on top.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = "ernestOS web"
SKIP_PREFIXES = ("tests/", ".github/", "mobile/android/", "mobile/www/", "mobile/node_modules/",
                 "mobile/brand/", "mobile/package", "mobile/capacitor", "docs/images/")
SKIP_NAMES = {"ZIP_MANIFEST.json"}
EXECUTABLE = (".sh", ".command")

START = """ErnestOS web — o'z kompyuteringizda (domen va hostingsiz)

1. Shu ZIP'ni oching: o'ng tugma -> "Barchasini chiqarish" (Extract All).
2. Windows: "ornatish-windows.bat" faylini ikki marta bosing.
   Mac: "ornatish-mac.command" -> o'ng tugma -> Open (birinchi marta).
        Ochilmasa: Terminal'da "bash " yozib, faylni oynaga sudrang -> Enter.
3. Dastur Documents\\PROJECTS\\ernestOS web papkasiga o'rnatiladi.
   Python 3.11+ yo'q bo'lsa, o'zi o'rnatiladi.
   Birinchi marta 2-5 daqiqa (kutubxonalar), keyin bir necha soniya.
4. Brauzer o'zi ochiladi: http://localhost:8000/app/
   Email bilan kiring. Kod shu yerda: http://localhost:8000/app/dev/mailbox

Keyingi safar: ish stolidagi "ErnestOS" yorlig'i (Windows)
yoki papkadagi start-local.sh (Mac/Linux).

Oynani yopmang - server shu oynada ishlaydi. To'xtatish: Ctrl+C.
"localhost refused to connect" - server yoqilmagan: qora oyna ochiq
va unda "ErnestOS ishlayapti" yozuvi bo'lishi kerak.
Ma'lumotlaringiz: ernestOS web\\data\\ernestos.db (zaxira uchun shu faylni nusxalang).
Batafsil: docs\\LOKAL.md
"""


def files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, check=True, capture_output=True, text=True).stdout.split("\n")
    keep = []
    for name in out:
        if not name or name.startswith(SKIP_PREFIXES) or name in SKIP_NAMES or name.endswith(".zip"):
            continue
        if (ROOT / name).is_file():
            keep.append(name)
    return sorted(keep)


def main() -> None:
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "ernestOS-web.zip"
    names = files()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        info = zipfile.ZipInfo(f"{FOLDER}/BOSHLASH.txt")
        info.external_attr = 0o644 << 16
        z.writestr(info, START.replace("\n", "\r\n").encode("utf-8"))
        for name in names:
            info = zipfile.ZipInfo.from_file(ROOT / name, f"{FOLDER}/{name}")
            # Unix permissions, so the Mac and Linux starters open by a double-click.
            info.external_attr = (0o755 if name.endswith(EXECUTABLE) else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (ROOT / name).read_bytes())
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        listed = set(z.namelist())
    for must in ("run_local.py", "start-local.bat", "ornatish-windows.bat", "ornatish-windows.ps1",
                 "ornatish-mac.command", "app.py", "web_app.py", "webapp/index.html", "webapp/web.js",
                 "mobile/src/login.html", "requirements.txt", "constraints-tested.txt"):
        assert f"{FOLDER}/{must}" in listed, must
    for never in (".env", ".env.local", "data/ernestos.db"):
        assert f"{FOLDER}/{never}" not in listed, never
    print(f"{len(names) + 1} files, {target.stat().st_size // 1024} KB: {target}")


if __name__ == "__main__":
    main()
