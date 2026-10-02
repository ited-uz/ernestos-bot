"""Build a clean, verifiable archive; never overwrite an existing deliverable."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

VERSION = "ErnestOS-v12.2"
root = Path(__file__).resolve().parents[1]
destination = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root.parent.parent / f"{VERSION}.zip"
if destination.exists():
    raise SystemExit(f"Refusing to overwrite: {destination}")
excluded = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", ".DS_Store"}
manifest = {}
with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for file in sorted(root.rglob("*")):
        relative = file.relative_to(root)
        if any(part in excluded for part in relative.parts):
            continue
        if file.is_symlink():
            raise SystemExit(f"Unexpected symlink: {relative}")
        if not file.is_file() or relative.as_posix() == "ZIP_MANIFEST.json":
            continue  # the manifest is generated below, never copied
        if (file.name.startswith(".env") and file.name != ".env.example") or file.suffix in {".pyc", ".db", ".sqlite", ".sqlite3", ".log", ".zip"}:
            continue
        data = file.read_bytes()
        manifest[str(relative)] = hashlib.sha256(data).hexdigest()
        archive.writestr(f"{VERSION}/" + relative.as_posix(), data)
    archive.writestr(f"{VERSION}/ZIP_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False))
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
    for name, expected in manifest.items():
        assert hashlib.sha256(archive.read(f"{VERSION}/" + name)).hexdigest() == expected
print(f"Verified {len(manifest)} files: {destination}")
print(f"SHA256: {hashlib.sha256(destination.read_bytes()).hexdigest()}")
