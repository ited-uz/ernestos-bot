"""Live check of the Google Drive / NotebookLM connector against real Google.

Run it only with a TEST account and its own test database — never the
founder's account or production data. It writes one synthetic note.

    DATABASE_URL=... GOOGLE_DRIVE_CLIENT_ID=... GOOGLE_DRIVE_CLIENT_SECRET=... \\
    CONNECTOR_TOKEN_KEY=... PUBLIC_URL=https://... \\
    python scripts/notebooklm_live_check.py --workspace 12 --test-account

Before: in the app (same server, same database) the test account connected
Google Drive and confirmed it. The script then, for real:
  1. refreshes the access token from the stored, encrypted refresh token;
  2. exports a synthetic note into a Google Doc in the ErnestOS folder;
  3. reads the Doc back from Drive and checks the note and the code are in it;
  4. edits the note, exports again, checks the SAME file id now has the edit;
  5. trashes the note, exports again, checks its text left the Doc;
  6. deletes the set and moves the Doc to the Drive trash.
NotebookLM itself has no consumer API: the script prints the manual steps and
the verification code to ask NotebookLM for. Tokens are never printed.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import notes  # noqa: E402
import notes_connector as conn  # noqa: E402
import notes_crypto  # noqa: E402
from db import NlmPack, NlmSourceSet, SessionLocal  # noqa: E402
from sqlalchemy import select  # noqa: E402


def doc_text(ws: int, set_id: int) -> tuple[str, str]:
    with SessionLocal() as s:
        pack = s.scalar(select(NlmPack).where(NlmPack.set_id == set_id, NlmPack.part == 1))
        acct = conn.account(s, ws)
        refresh = notes_crypto.decrypt(acct.refresh_token_enc, purpose="refresh")
    google = conn.Google()
    try:
        return pack.drive_file_id, google.export_text(google.access(refresh), pack.drive_file_id)
    finally:
        google.close()


def drain(limit=20):
    for _ in range(limit):
        if not conn.process_due():
            return
        time.sleep(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=int, required=True)
    ap.add_argument("--test-account", action="store_true", help="confirm this workspace is a test account")
    a = ap.parse_args()
    if not a.test_account:
        print("Refusing: pass --test-account to confirm the workspace belongs to a test account.")
        return 2
    missing = conn.missing_config()
    if missing:
        print("BLOCKED: missing settings:", ", ".join(missing))
        return 3
    db.init_db()
    ws = a.workspace
    with SessionLocal() as s:
        acct = conn.account(s, ws)
        if acct is None or acct.status != "connected":
            print("BLOCKED: this workspace has no confirmed Google Drive connection. Connect it in the app first.")
            return 3
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        note = notes.create(s, ws, title=f"ErnestOS live check {stamp}", body="LIVE-CHECK-ALPHA\n- synthetic note",
                            status="reviewed")
        row = conn.create_set(s, ws, name=f"Live check {stamp}", note_ids=[note.id])
        set_id, note_id, code = row.id, note.id, row.verify_code
    results = []

    def check(name, ok):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + name)

    drain()
    file_id, text = doc_text(ws, set_id)
    check("doc created in Drive and readable", bool(file_id) and "LIVE-CHECK-ALPHA" in text)
    check("verification code is in the doc", code in text)
    with SessionLocal() as s:
        n = s.get(db.Note, note_id)
        notes.update(s, ws, note_id, {"body": "LIVE-CHECK-BETA\n- edited"}, n.revision)
        conn.sync(s, ws, set_id)
    drain()
    file_id2, text2 = doc_text(ws, set_id)
    check("update kept the same Drive file id", file_id2 == file_id)
    check("update reached the doc", "LIVE-CHECK-BETA" in text2 and "LIVE-CHECK-ALPHA" not in text2)
    with SessionLocal() as s:
        notes.trash(s, ws, note_id)
    drain()
    _, text3 = doc_text(ws, set_id)
    check("a trashed note leaves the doc", "LIVE-CHECK-BETA" not in text3)
    print("\nNotebookLM (manual, no consumer API):")
    print(f"  1. Open https://docs.google.com/document/d/{file_id}/edit to see the doc.")
    print("  2. In NotebookLM: Add source → Google Docs → pick it.")
    print(f"  3. Ask NotebookLM for the ErnestOS verification code; it must answer {code}.")
    print("  4. Paste the notebook link and the answer in the app to mark it verified.")
    keep = input("\nKeep the doc for the NotebookLM step? [y/N] ").strip().lower() == "y" if sys.stdin.isatty() else False
    if not keep:
        with SessionLocal() as s:
            conn.delete_set(s, ws, set_id, trash_docs=True)
        drain()
        with SessionLocal() as s:
            gone = s.get(NlmSourceSet, set_id) is None
        check("set deleted and doc moved to the Drive trash", gone)
    print(f"\n{sum(results)}/{len(results)} live checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
