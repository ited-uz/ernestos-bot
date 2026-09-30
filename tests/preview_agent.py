"""Local UI-only preview with fake data. NEVER run this as the production server.

python tests/preview_agent.py
Open http://127.0.0.1:8765/?preview&agent&lang=uz
No database, Telegram token, provider key or external AI calls are used.
"""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_text import TEXT  # noqa: E402


class Handler(SimpleHTTPRequestHandler):
    consent = False
    drafts = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "webapp"), **kwargs)

    def reply(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/agent/meta":
            from urllib.parse import parse_qs
            referer = urlsplit(self.headers.get("Referer", ""))
            lang = parse_qs(referer.query).get("lang", ["uz"])[0]
            return self.reply({"enabled": True, "configured": True, "provider": "DEMO — no API calls", "consent": Handler.consent,
                               "audio_seconds": 120, "audio_bytes": 10485760, "strings": TEXT.get(lang, TEXT["uz"])})
        if path == "/api/agent/inbox":
            return self.reply({"drafts": list(Handler.drafts.values())[::-1]})
        if path.startswith("/api/agent/drafts/"):
            return self.reply(Handler.drafts.get(path.split("/")[-1], {}))
        return super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 10485760))
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            data = {}
        if path == "/api/agent/consent":
            Handler.consent = bool(data.get("accepted"))
            return self.reply({"consent": Handler.consent})
        if path in {"/api/agent/text", "/api/agent/audio"}:
            old = Handler.drafts.get(data.get("draft_id"), {})
            draft = {"id": old.get("id", uuid.uuid4().hex), "status": "ready", "revision": old.get("revision", 0) + 1,
                     "transcript": data.get("text", "DEMO audio — not transcribed"), "language": "mixed",
                     "preview": "DEMO · Qo‘shish · Vazifa · Shaxsiy\nAbdulbosid bilan uchrashuv\nLoyiha: Alohida (yakka vazifa)\nSana: 2026-10-01\nVaqt: 10:00\nIzoh: Uchrashuvda barcha muammolarni hal qilish.\nMuhimlik: O‘rta", "error": None, "result": []}
            Handler.drafts[draft["id"]] = draft
            return self.reply(draft)
        parts = path.split("/")
        if len(parts) == 6 and parts[4] in Handler.drafts:
            draft = Handler.drafts[parts[4]]
            if data.get("revision") != draft["revision"]:
                return self.reply({"detail": "stale_draft"}, 409)
            draft["status"] = {"confirm": "executed", "cancel": "cancelled", "retry": "ready"}.get(parts[-1], "ready")
            return self.reply(draft)
        return self.reply({"detail": "not_found"}, 404)

    def do_DELETE(self):
        Handler.drafts.clear()
        Handler.consent = False
        return self.reply({"ok": True})


if __name__ == "__main__":
    print("UI-only preview: http://127.0.0.1:8765/?preview&agent&lang=uz", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
