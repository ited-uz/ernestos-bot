#!/usr/bin/env python3
"""ErnestOS on this computer — one command, no domain, no hosting.

    python run_local.py            # Windows: double-click start-local.bat
    python run_local.py --lan      # also open it from a phone on the same Wi-Fi
    python run_local.py --port 8080 --no-browser

The first run makes everything it needs, once:

  * .venv/            Python packages (requirements.txt)
  * .env.local        settings for this computer (kept out of Git); optional
                      keys — a Telegram bot, an AI key — are added here
  * data/ernestos.db  your data (SQLite). Copy this file to back it up.
  * data/mail/        sign-in codes. There is no mail service on a local run:
                      the code is shown at /app/dev/mailbox, on this computer only.

Then it starts the server and opens http://localhost:8000/app/ in the browser.
Ctrl+C stops it; your data stays in data/.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
DATA = ROOT / "data"
ENV_FILE = ROOT / ".env.local"
WIN = os.name == "nt"


def say(text: str) -> None:
    print(text, flush=True)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if WIN else "bin/python")


def ensure_python() -> None:
    if sys.version_info < (3, 11):
        say(f"Python 3.11 yoki yangisi kerak (sizda {sys.version.split()[0]}). https://www.python.org/downloads/")
        sys.exit(1)


def ensure_packages() -> None:
    """The virtual environment and its packages — installed again only when
    requirements change."""
    if not venv_python().exists():
        say("• Python muhiti yaratilmoqda (.venv)…")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    wanted = hashlib.sha256(b"".join((ROOT / f).read_bytes() for f in
                                     ("requirements.txt", "constraints-tested.txt"))).hexdigest()
    stamp = VENV / ".ernestos-installed"
    if stamp.exists() and stamp.read_text().strip() == wanted:
        return
    say("• Kutubxonalar o'rnatilmoqda (birinchi marta bir necha daqiqa)…")
    py = str(venv_python())
    subprocess.check_call([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
    subprocess.check_call([py, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                           "-r", str(ROOT / "requirements.txt")], cwd=ROOT)
    stamp.write_text(wanted)


ENV_TEMPLATE = """# ErnestOS — settings for this computer (run_local.py). Not in Git.
# Change what you need and start again. Empty means "off".

ENVIRONMENT=local
TZ=Asia/Tashkent
PUBLIC_URL=http://localhost:{port}
DATABASE_URL={database}

# Sign-in codes go to files; read them at http://localhost:{port}/app/dev/mailbox
EMAIL_PROVIDER=file
EMAIL_OUTBOX_DIR={mail}

# Seals Google Drive tokens (Qaydlar → NotebookLM). Made for this computer.
CONNECTOR_TOKEN_KEY={key}

# ── Optional ────────────────────────────────────────────────────────────
# A Telegram bot of your own (@BotFather). A TEST bot: the same token must
# not run on a server at the same time. Empty: reminders and reports go to
# the app's inbox instead.
BOT_TOKEN=
BOT_USERNAME=

# Voice and the AI assistant (https://console.groq.com/keys). Also needs FFmpeg.
AGENT_ENABLED=false
GROQ_API_KEY=

# Real email instead of the local mailbox (then remove EMAIL_PROVIDER=file):
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USER=you@gmail.com
# SMTP_PASSWORD=app-password
# EMAIL_FROM=ErnestOS <you@gmail.com>

# Google sign-in: a "Web application" OAuth client with the redirect URI
# http://localhost:{port}/api/auth/oauth/google/callback
# GOOGLE_CLIENT_ID=
# GOOGLE_CLIENT_SECRET=
"""


def ensure_settings(port: int) -> None:
    DATA.mkdir(exist_ok=True)
    (DATA / "mail").mkdir(exist_ok=True)
    if ENV_FILE.exists():
        return
    database = "sqlite:///" + (DATA / "ernestos.db").as_posix()
    ENV_FILE.write_text(ENV_TEMPLATE.format(port=port, database=database, mail=(DATA / "mail").as_posix(),
                                            key=secrets.token_urlsafe(32)), encoding="utf-8")
    say(f"• Sozlamalar yaratildi: {ENV_FILE.name}")


def read_settings() -> dict:
    out = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    say("Bo'sh port topilmadi (8000–8019). --port bilan boshqasini bering.")
    sys.exit(1)


def lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def wait_ready(url: str, server: subprocess.Popen, seconds: int = 60) -> bool:
    until = time.time() + seconds
    while time.time() < until:
        if server.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url + "/api/version", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="ErnestOS on this computer")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--lan", action="store_true", help="also reachable from a phone on the same Wi-Fi")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--setup-only", action="store_true", help="install and configure, then stop")
    args = ap.parse_args()

    ensure_python()
    ensure_packages()
    port = free_port(args.port)
    ensure_settings(port)
    if args.setup_only:
        say("• Tayyor. Ishga tushirish: python run_local.py")
        return

    env = {**os.environ, **read_settings(), "PYTHONUNBUFFERED": "1", "ERNEST_NO_DOTENV": "1"}
    url = f"http://localhost:{port}"
    # Another port than the first run's (that one was busy): sign-in links follow.
    if env.get("PUBLIC_URL", "").startswith("http://localhost"):
        env["PUBLIC_URL"] = url
    if env.get("ENVIRONMENT") != "local":
        say("• Diqqat: .env.local'da ENVIRONMENT=local emas.")
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    say(f"• Server ishga tushmoqda: {url}")
    server = subprocess.Popen([str(venv_python()), "-m", "uvicorn", "app:app", "--host", host,
                               "--port", str(port), "--no-server-header"], cwd=ROOT, env=env)
    try:
        if not wait_ready(f"http://127.0.0.1:{port}", server):
            say("Server ishga tushmadi — yuqoridagi xabarni o'qing.")
            server.terminate()
            sys.exit(1)
        say("")
        say("  ✅ ErnestOS ishlayapti")
        say(f"     Ilova:          {url}/app/")
        say(f"     Kirish kodlari: {url}/app/dev/mailbox   (faqat shu kompyuterda)")
        if args.lan:
            ip = lan_address()
            if ip:
                say(f"     Telefondan:     http://{ip}:{port}/app/   (bir xil Wi-Fi; kodni kompyuterdagi")
                say("                     pochta qutisidan oling. Ovoz telefonda https talab qiladi.)")
        say("     Ma'lumotlar:    data/ernestos.db   ·   to'xtatish: Ctrl+C")
        say("")
        if not args.no_browser:
            webbrowser.open(url + "/app/")
        server.wait()
    except KeyboardInterrupt:
        say("\n• To'xtatilmoqda…")
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
