"""
Notes in and out as Markdown: an export anyone can open, and an import of an
Obsidian vault or a Notion export that is shown before anything is written.

Export: a ZIP of `.md` files with front matter, the attachments beside them,
and `_ernestos/manifest.json` with every field — nothing a note has is lost.
`format=obsidian` writes links between notes as `[[wikilinks]]` for a vault.

Import: the archive is read in memory, never unpacked to disk, and nothing in
it is executed — DataviewJS, Dataview and Templater blocks stay plain text.
Guarded against path traversal, symlinks, encrypted entries and ZIP bombs
(entry count, per-file and total size, compression ratio). Re-importing the
same archive is a no-op: notes are matched by their ErnestOS id, then by the
source's own id or path, then by identical content.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
import stat
import zipfile
from sqlalchemy import delete as sql_delete, func, or_, select
from sqlalchemy.orm import Session

import notes
import services as svc
from db import (
    Note, NoteAttachment, NoteImportBatch, NoteNotebook, NoteNotebookMember, NoteRelation, utcnow,
)

UPLOAD_MAX = 30 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_TOTAL = 150 * 1024 * 1024
MAX_MD = 2 * 1024 * 1024
MAX_RATIO = 100
MAX_NOTES = 3000

#: Never imported, whatever the vault says.
EXCLUDED_PARTS = {".obsidian", ".git", ".trash", "__MACOSX", ".github", "node_modules"}
EXCLUDED_PREFIXES = ("_system/examples", "AI/inbox", "AI/exports")
EXCLUDED_FILES = {"AI/log.md", ".DS_Store"}
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf"}


class ImportError_(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# Front matter (a small, safe YAML subset; never a full YAML loader)
# ---------------------------------------------------------------------------

def _scalar(raw: str):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] == raw[-1] == '"' and len(raw) >= 2:
        try:
            return json.loads(raw)
        except ValueError:
            return raw[1:-1]
    if raw[0] == raw[-1] == "'" and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        try:
            return json.loads(raw)
        except ValueError:
            return [_scalar(x) for x in re.split(r",(?=(?:[^\"']*[\"'][^\"']*[\"'])*[^\"']*$)", inner)]
    low = raw.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    return raw


def parse_front_matter(text: str) -> tuple[dict, str, list[str]]:
    """(properties, body, ignored keys). Only `key: value`, flow lists and
    `- item` block lists are read; anything else is reported as ignored."""
    if not text.startswith("---"):
        return {}, text, []
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text, []
    end = next((i for i in range(1, min(len(lines), 400)) if lines[i].strip() in ("---", "...")), None)
    if end is None:
        return {}, text, []
    props: dict = {}
    ignored: list[str] = []
    key = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_\-. ]{1,60}):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            key = m.group(1).strip()
            value = m.group(2)
            props[key] = _scalar(value) if value.strip() else []
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and key is not None and isinstance(props.get(key), list):
            props[key].append(_scalar(item.group(1)))
            continue
        if key and key not in ignored:
            ignored.append(key)
            props.pop(key, None)
    body = "\n".join(lines[end + 1:])
    return props, body.lstrip("\n"), ignored


def _yaml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_yaml(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def front_matter(props: dict) -> str:
    return "---\n" + "".join(f"{k}: {_yaml(v)}\n" for k, v in props.items()) + "---\n"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*#^\[\]]+')


def safe_name(value: str, fallback: str = "Untitled") -> str:
    name = _UNSAFE.sub(" ", str(value or "")).strip().strip(".")
    name = " ".join(name.split())[:100]
    return name or fallback


def export_zip(s: Session, ws: int, *, notebook_id: int | None = None, fmt: str = "ernestos",
               include_trash: bool = False) -> bytes:
    rows_q = select(Note).where(Note.workspace_id == ws)
    if not include_trash:
        rows_q = rows_q.where(Note.trashed_at.is_(None))
    if notebook_id:
        book = s.get(NoteNotebook, int(notebook_id))
        if book is None or book.workspace_id != ws:
            raise svc.NotFound("notebook")
        rows_q = rows_q.where(Note.id.in_(select(NoteNotebookMember.note_id).where(
            NoteNotebookMember.notebook_id == book.id)))
    rows = s.scalars(rows_q.order_by(Note.id)).all()
    ids = [n.id for n in rows]
    tag_map, book_map = notes._tags_of(s, ids), notes._books_of(s, ids)
    books = {b.id: b for b in s.scalars(select(NoteNotebook).where(NoteNotebook.workspace_id == ws))}
    used: set[str] = set()
    paths: dict[int, str] = {}
    for n in rows:
        folder = "Daily" if n.daily_date else (safe_name(books[book_map[n.id][0]].name)
                                               if book_map[n.id] and book_map[n.id][0] in books else "")
        base = safe_name(n.title, fallback=f"Note {n.uid[:8]}")
        path = f"{folder}/{base}.md" if folder else f"{base}.md"
        k = 2
        while path.lower() in used:
            path = f"{folder}/{base} ({k}).md" if folder else f"{base} ({k}).md"
            k += 1
        used.add(path.lower())
        paths[n.id] = path
    by_uid = {n.uid: n for n in rows}
    atts = s.scalars(select(NoteAttachment).where(NoteAttachment.workspace_id == ws,
                                                  NoteAttachment.note_id.in_(ids or [0]))).all()
    att_path = {a.uid: f"attachments/{a.uid[:8]}-{safe_name(a.filename, 'file')}" for a in atts}
    buf = io.BytesIO()
    manifest = {"format": "ernestos-notes", "version": 1, "exported_at": utcnow().isoformat() + "Z",
                "notebooks": [{"id": b.id, "name": b.name, "color": b.color, "icon": b.icon,
                               "description": b.description} for b in books.values()],
                "notes": []}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in rows:
            body = n.body
            depth = paths[n.id].count("/")
            up = "../" * depth
            if fmt == "obsidian":
                def wiki(m, by_uid=by_uid, paths=paths):
                    target = by_uid.get(m.group(2))
                    if target is None:
                        return m.group(0)
                    name = paths[target.id][:-3]
                    label = m.group(1)
                    return f"[[{name}|{label}]]" if label and label != target.title else f"[[{name}]]"
                body = re.sub(r"\[([^\]]*)\]\(note:([0-9a-f]{26})\)", wiki, body)
            body = re.sub(r"\(attachment:([0-9a-f]{32})\)",
                          lambda m: f"({up}{att_path[m.group(1)]})" if m.group(1) in att_path else m.group(0), body)
            props = {"ernestos_id": n.uid, "title": n.title, "kind": n.kind, "status": n.status,
                     "tags": tag_map[n.id],
                     "notebooks": [books[b].name for b in book_map[n.id] if b in books],
                     "created": n.created_at.isoformat() + "Z", "updated": n.updated_at.isoformat() + "Z"}
            if n.pinned_at:
                props["pinned"] = True
            if n.archived_at:
                props["archived"] = True
            if n.trashed_at:
                props["trashed"] = True
            if n.daily_date:
                props["daily"] = n.daily_date.isoformat()
            if n.source_url:
                props["source"] = n.source_url
            if n.ai_excluded:
                props["ai_excluded"] = True
            z.writestr(paths[n.id], front_matter(props) + "\n" + body)
            manifest["notes"].append({
                "uid": n.uid, "path": paths[n.id], "title": n.title, "kind": n.kind,
                "template": n.template, "status": n.status, "tags": tag_map[n.id],
                "notebook_ids": book_map[n.id], "pinned": n.pinned_at is not None,
                "archived": n.archived_at is not None, "trashed": n.trashed_at is not None,
                "daily_date": n.daily_date.isoformat() if n.daily_date else None,
                "source_url": n.source_url, "ai_excluded": bool(n.ai_excluded),
                "revision": n.revision, "content_hash": n.content_hash,
                "created_at": n.created_at.isoformat() + "Z", "updated_at": n.updated_at.isoformat() + "Z",
                "body_sha256": hashlib.sha256(n.body.encode()).hexdigest(),
                "relations": [{"kind": r.kind, "target_id": r.target_id, "rel": r.rel}
                              for r in s.scalars(select(NoteRelation).where(NoteRelation.note_id == n.id))],
            })
        for a in atts:
            z.writestr(att_path[a.uid], s.scalar(select(NoteAttachment.data).where(NoteAttachment.id == a.id)))
        if fmt == "ernestos":
            # The exact bodies, for a byte-for-byte restore into ErnestOS.
            z.writestr("_ernestos/bodies.json", json.dumps({n.uid: n.body for n in rows}, ensure_ascii=False))
        z.writestr("_ernestos/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        z.writestr("README.md", "# ErnestOS notes export\n\nOne Markdown file per note, with its "
                   "properties in the front matter. `_ernestos/manifest.json` lists every field; "
                   "importing this ZIP back into ErnestOS restores the notes without duplicates.\n")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Reading an archive safely
# ---------------------------------------------------------------------------

def _clean_path(name: str) -> str | None:
    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _entries(data: bytes, depth: int = 0) -> list[tuple[str, bytes]]:
    """(path, bytes) of every regular file, bounded; nested Notion ZIPs once."""
    if len(data) > UPLOAD_MAX:
        raise ImportError_("archive_too_large")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportError_("not_a_zip") from None
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ImportError_("too_many_files")
    out: list[tuple[str, bytes]] = []
    total = 0
    for info in infos:
        if info.is_dir() or info.flag_bits & 0x1:      # directory, or encrypted
            continue
        mode = info.external_attr >> 16
        if mode and stat.S_ISLNK(mode):
            continue
        path = _clean_path(info.filename)
        if path is None:
            raise ImportError_("unsafe_path")
        if info.file_size > MAX_TOTAL or (info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO
                                          and info.file_size > 1024 * 1024):
            raise ImportError_("zip_bomb")
        limit = MAX_MD if path.lower().endswith((".md", ".csv")) else notes.ATTACH_FILE_MAX
        if path.lower().endswith(".zip") and depth == 0:
            limit = UPLOAD_MAX
        if info.file_size > limit:
            continue
        with z.open(info) as fh:
            blob = fh.read(limit + 1)
        if len(blob) > limit:
            continue
        total += len(blob)
        if total > MAX_TOTAL:
            raise ImportError_("zip_bomb")
        if path.lower().endswith(".zip") and depth == 0:
            out.extend(_entries(blob, depth + 1))
        else:
            out.append((path, blob))
    return out


def _strip_root(entries: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    roots = {p.split("/")[0] for p, _ in entries if "/" in p}
    singles = [p for p, _ in entries if "/" not in p]
    if len(roots) == 1 and not singles:
        root = roots.pop()
        if root not in EXCLUDED_PARTS:
            return [(p[len(root) + 1:], b) for p, b in entries]
    return entries


def excluded(path: str) -> str | None:
    parts = path.split("/")
    if any(p in EXCLUDED_PARTS for p in parts):
        return "excluded_folder"
    if path in EXCLUDED_FILES or parts[-1] in EXCLUDED_FILES:
        return "excluded_file"
    if any(path == pre or path.startswith(pre + "/") for pre in EXCLUDED_PREFIXES):
        return "excluded_folder"
    return None


_NOTION_ID = re.compile(r"\s([0-9a-f]{32})(?=\.md$|\.csv$|$)")


def _notion_clean(path: str) -> str:
    """A Notion path without the 32-hex ids Notion appends to every name."""
    return "/".join(_NOTION_ID.sub("", part).strip() for part in path.split("/")) if path else ""


_INLINE_TAG = re.compile(r"(?<![\w&/#])#([\w\-/]{1,40})", re.UNICODE)
_WIKI_EMBED = re.compile(r"!\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def _decode(blob: bytes) -> str | None:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _inline_tags(body: str) -> list[str]:
    no_code = re.sub(r"```.*?```|`[^`]*`", "", body, flags=re.S)
    return [t for t in _INLINE_TAG.findall(no_code) if not t.isdigit()][:20]


def detect(entries: list[tuple[str, bytes]]) -> str:
    names = [p for p, _ in entries]
    if any(p.startswith("_ernestos/manifest.json") for p in names):
        return "ernestos"
    if any(".obsidian/" in p or p.startswith(".obsidian") for p in names):
        return "obsidian"
    if any(_NOTION_ID.search(p.rsplit("/", 1)[-1]) for p in names if p.endswith((".md", ".csv"))):
        return "notion"
    return "obsidian"


def parse(data: bytes, source: str = "auto") -> dict:
    """Read an archive into a preview. Writes nothing."""
    entries = _strip_root(_entries(data))
    kind = detect(entries) if source == "auto" else source
    if kind not in ("obsidian", "notion", "ernestos"):
        raise ImportError_("bad_source")
    files = {p: b for p, b in entries}
    bodies_exact = {}
    if "_ernestos/bodies.json" in files:
        try:
            bodies_exact = json.loads(files["_ernestos/bodies.json"].decode())
        except ValueError:
            bodies_exact = {}
    items, skipped, warnings = [], [], set()
    md_titles_by_dir: dict[str, set[str]] = {}
    for path, blob in sorted(entries):
        why = excluded(path)
        low = path.lower()
        if why:
            skipped.append({"path": path, "reason": why})
            continue
        if path.startswith("_ernestos/") or (path == "README.md" and kind == "ernestos"):
            continue
        if not low.endswith(".md"):
            continue
        text = _decode(blob)
        if text is None:
            skipped.append({"path": path, "reason": "not_text"})
            continue
        props, body, ignored = parse_front_matter(text.replace("\r\n", "\n"))
        if ignored:
            warnings.add("ignored_properties")
        name = path.rsplit("/", 1)[-1][:-3]
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        external = ""
        title = str(props.get("title") or "").strip()
        if kind == "notion":
            m = _NOTION_ID.search(name + ".md")
            if m:
                external = "notion:" + m.group(1)
                name = name[: m.start()].strip()
            first = re.match(r"^# (.+)\n", body)
            if first:
                title = title or first.group(1).strip()
                body = body[first.end():].lstrip("\n")
            folder = _notion_clean(folder)
        else:
            external = "obsidian:" + path
        title = title or name
        if "```dataviewjs" in body or "```dataview" in body:
            warnings.add("dataview_kept_as_text")
        if "<%" in body and "%>" in body:
            warnings.add("templater_kept_as_text")
        uid = str(props.get("ernestos_id") or "")
        if uid and uid in bodies_exact:
            body = bodies_exact[uid]
        tags = props.get("tags") or props.get("tag") or []
        if isinstance(tags, str):
            tags = [t for t in re.split(r"[,\s]+", tags) if t]
        tags = [str(t) for t in tags] + ([] if kind == "ernestos" else _inline_tags(body))
        try:
            tags = notes.clean_tags(tags[:40])[:notes.TAGS_PER_NOTE]
        except notes.Invalid:
            tags = []
        books = props.get("notebooks") if kind == "ernestos" else None
        if not books:
            top = folder.split("/")[0] if folder else ""
            books = [top] if top and top != "Daily" else []
        embeds = [m for m in _WIKI_EMBED.findall(body)] + [m[1] for m in _MD_IMAGE.findall(body)
                                                           if not m[1].startswith(("http", "attachment:", "note:"))]
        kind_v = props.get("kind") if props.get("kind") in notes.KINDS else "note"
        md_titles_by_dir.setdefault(folder, set()).add(title.lower())
        items.append({
            "path": path, "title": notes.clean_title(title), "body": body[:notes.BODY_MAX],
            "too_long": len(body) > notes.BODY_MAX,
            "tags": tags, "notebooks": [notes.clean_title(b)[:80] for b in books if str(b).strip()][:5],
            "kind": kind_v, "status": "reviewed" if props.get("status") == "reviewed" else "inbox",
            "pinned": bool(props.get("pinned")), "archived": bool(props.get("archived")),
            "source_url": str(props.get("source") or props.get("url") or "")[:1000],
            "ai_excluded": bool(props.get("ai_excluded")),
            "uid": uid if re.fullmatch(r"[0-9a-f]{26}", uid) else "",
            "external_id": external[:300], "embeds": embeds[:50], "folder": folder,
        })
        if len(items) > MAX_NOTES:
            raise ImportError_("too_many_notes")
    if kind == "notion":
        for path, blob in sorted(entries):
            if not path.lower().endswith(".csv") or excluded(path):
                continue
            text = _decode(blob)
            if text is None:
                continue
            m = _NOTION_ID.search(path.rsplit("/", 1)[-1])
            # Rows that have their own page are imported as that page.
            db_dir = _notion_clean(path[:-4])
            known = md_titles_by_dir.get(db_dir, set())
            db_name = db_dir.rsplit("/", 1)[-1]
            top = db_dir.split("/")[0] if "/" in db_dir else ''
            reader = csv.reader(io.StringIO(text))
            header = next(reader, None)
            if not header:
                continue
            for k, row in enumerate(reader):
                if not row or not row[0].strip() or row[0].strip().lower() in known:
                    continue
                props = "\n".join(f"- **{h}**: {v}" for h, v in zip(header[1:], row[1:]) if v.strip())
                items.append({
                    "path": f"{path}#{k + 1}", "title": notes.clean_title(row[0]), "body": props,
                    "too_long": False, "tags": notes.clean_tags([db_name])[:1] if db_name else [],
                    "notebooks": [notes.clean_title(top)[:80]] if top else [],
                    "kind": "note", "status": "inbox", "pinned": False, "archived": False,
                    "source_url": "", "ai_excluded": False, "uid": "",
                    "external_id": f"notion:{m.group(1) if m else path}:{k + 1}", "embeds": [], "folder": "",
                })
                if len(items) > MAX_NOTES:
                    raise ImportError_("too_many_notes")
    return {"source": kind, "items": items, "skipped": skipped, "warnings": sorted(warnings),
            "files": files}


def _resolve(s: Session, ws: int, item: dict) -> tuple[str, Note | None]:
    """What to do with one parsed note: new, update, or skip_duplicate."""
    found = None
    if item["uid"]:
        found = notes.by_uid(s, ws, item["uid"])
    if found is None and item["external_id"]:
        found = s.scalar(select(Note).where(Note.workspace_id == ws, Note.external_id == item["external_id"]))
    h = notes.content_hash(item["title"], item["body"])
    if found is not None:
        same = h in (found.content_hash, found.import_hash)
        return ("skip_duplicate" if same else "update"), found
    dup = s.scalar(select(Note).where(Note.workspace_id == ws,
                                      or_(Note.content_hash == h, Note.import_hash == h)).limit(1))
    if dup is not None:
        return "skip_duplicate", dup
    return "new", None


def preview(s: Session, ws: int, data: bytes, source: str = "auto") -> dict:
    notes.tidy(s)
    parsed = parse(data, source)
    # One import waits for confirmation at a time: a new preview replaces it,
    # so repeated uploads cannot pile archives up in the database.
    s.execute(sql_delete(NoteImportBatch).where(NoteImportBatch.workspace_id == ws,
                                                NoteImportBatch.status == "preview"))
    rows = []
    for it in parsed["items"]:
        action, found = _resolve(s, ws, it)
        rows.append({"path": it["path"], "title": it["title"], "action": action,
                     "notebooks": it["notebooks"], "tags": it["tags"], "words": notes.count_words(it["body"]),
                     "too_long": it["too_long"], "attachments": len(it["embeds"]),
                     "existing_id": found.id if found is not None else None})
    counts = {k: sum(1 for r in rows if r["action"] == k) for k in ("new", "update", "skip_duplicate")}
    counts["excluded"] = len(parsed["skipped"])
    summary = {"source": parsed["source"], "items": rows, "skipped": parsed["skipped"][:500],
               "warnings": parsed["warnings"], "counts": counts}
    token = secrets.token_hex(16)
    s.add(NoteImportBatch(workspace_id=ws, token=token, source=parsed["source"],
                          payload=json.dumps(summary, ensure_ascii=False), archive=data,
                          sha256=hashlib.sha256(data).hexdigest()))
    s.commit()
    return {"token": token, **summary}


def _book(s: Session, ws: int, name: str, cache: dict) -> int:
    key = name.lower()
    if key in cache:
        return cache[key]
    found = s.scalar(select(NoteNotebook).where(NoteNotebook.workspace_id == ws,
                                                func.lower(NoteNotebook.name) == key))
    if found is None:
        found = NoteNotebook(workspace_id=ws, name=name[:80], color="slate")
        s.add(found)
        s.flush()
    cache[key] = found.id
    return found.id


def commit(s: Session, ws: int, token: str, *, exclude_paths=()) -> dict:
    """Write the previewed notes. Idempotent: the same archive again changes
    nothing; a batch is used once."""
    batch = s.scalar(select(NoteImportBatch).where(NoteImportBatch.workspace_id == ws,
                                                   NoteImportBatch.token == str(token)[:40]))
    if batch is None or batch.status != "preview":
        raise svc.NotFound("import")
    data = s.scalar(select(NoteImportBatch.archive).where(NoteImportBatch.id == batch.id))
    if not data:
        raise svc.NotFound("import")
    parsed = parse(data, batch.source)
    skip = set(exclude_paths or [])
    books: dict[str, int] = {}
    made, updated, same, failed = 0, 0, 0, 0
    cap = notes._storage_cap(s, ws)
    used = s.scalar(select(func.coalesce(func.sum(NoteAttachment.size), 0))
                    .where(NoteAttachment.workspace_id == ws)) or 0
    files = parsed["files"]
    by_name = {}
    for p in files:
        by_name.setdefault(p.rsplit("/", 1)[-1].lower(), p)
    for it in parsed["items"]:
        if it["path"] in skip:
            continue
        action, found = _resolve(s, ws, it)
        if action == "skip_duplicate":
            same += 1
            continue
        try:
            book_ids = [_book(s, ws, b, books) for b in it["notebooks"] if b]
            if action == "update":
                grows = len(it["title"]) + len(it["body"]) - len(found.title) - len(found.body)
                if grows > 0:
                    notes.check_text_room(s, ws, grows)
                notes._snapshot(s, found, "import", force=True)
                found.title, found.body = it["title"], it["body"]
                found.content_hash = notes.content_hash(found.title, found.body)
                found.words = notes.count_words(found.body)
                found.revision += 1
                found.updated_at = utcnow()
                notes._set_tags(s, ws, found, it["tags"])
                if book_ids:
                    notes._set_books(s, ws, found, book_ids)
                note = found
                updated += 1
            else:
                note = notes.create(s, ws, title=it["title"], body=it["body"], kind=it["kind"],
                                    tags=it["tags"], notebook_ids=book_ids, status=it["status"],
                                    source_url=it["source_url"] if re.match(r"^https?://", it["source_url"]) else "",
                                    import_source=batch.source, external_id=it["external_id"],
                                    ai_excluded=it["ai_excluded"], commit=False)
                if it["uid"] and notes.by_uid(s, ws, it["uid"]) is None:
                    note.uid = it["uid"]
                if it["pinned"]:
                    note.pinned_at = utcnow()
                if it["archived"]:
                    note.archived_at = utcnow()
                made += 1
            note.import_hash = notes.content_hash(it["title"], it["body"])
            # Embedded files the archive carries become attachments.
            body = note.body
            for ref in it["embeds"]:
                path = next((c for c in (_join(it["folder"], ref), ref) if c in files), None) \
                    or by_name.get(ref.rsplit("/", 1)[-1].lower())
                ext = "." + ref.rsplit(".", 1)[-1].lower() if "." in ref else ""
                if not path or ext not in IMAGE_EXT:
                    continue
                blob = files[path]
                if used + len(blob) > cap or len(blob) > notes.ATTACH_FILE_MAX:
                    continue
                mime = IMAGE_EXT[ext]
                magic = notes.ATTACH_TYPES[mime]
                if magic and not any(blob.startswith(m) for m in magic):
                    continue
                att = NoteAttachment(workspace_id=ws, note_id=note.id, uid=secrets.token_hex(16),
                                     filename=notes.safe_filename(path.rsplit("/", 1)[-1]), mime=mime,
                                     size=len(blob), sha256=hashlib.sha256(blob).hexdigest(), data=blob)
                s.add(att)
                used += len(blob)
                body = body.replace(f"![[{ref}]]", f"![{ref}](attachment:{att.uid})")
                body = re.sub(r"(!\[[^\]]*\])\(" + re.escape(ref) + r"\)", lambda m: f"{m.group(1)}(attachment:{att.uid})", body)
            if body != note.body:
                note.body = body
                note.content_hash = notes.content_hash(note.title, body)
            s.flush()
        except notes.Invalid:
            failed += 1
    # Links are resolved once every note exists.
    for n in s.scalars(select(Note).where(Note.workspace_id == ws, Note.import_source == batch.source)):
        notes._sync_links(s, ws, n)
    batch.status = "done"
    batch.archive = None
    s.commit()
    return {"created": made, "updated": updated, "unchanged": same, "failed": failed,
            "notebooks": len(books)}


def _join(folder: str, ref: str) -> str:
    """`ref` (as written in a note in `folder`) as an archive path."""
    parts = [p for p in folder.split("/") if p] if not ref.startswith("/") else []
    for piece in ref.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece not in ("", "."):
            parts.append(piece)
    return "/".join(parts)
