"""Check frozen hashes and run existing validators in an isolated temporary copy."""
from __future__ import annotations

import hashlib
import gzip
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def check_privacy(records):
    """Catch common accidental disclosures without printing matched values."""
    patterns = {
        "local absolute path": rb"/(?:Users|home|workspace|mnt|private/var)/[^\s\"<>]+",
        "Windows user path": rb"[A-Za-z]:[\\/]+Users[\\/]+",
        "GitHub credential": rb"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}",
        "private key": rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
        "credential assignment": rb"(?i)[\"']?(?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password|authorization)[\"']?\s*[:=]\s*[\"'][^\"'\s]{8,}",
    }
    for entry in records:
        path = ROOT / entry["path"]
        # Fonts retain upstream author attribution; do not modify licensed assets.
        if path.suffix in {".ttf", ".otf"}:
            continue
        raw = path.read_bytes()
        if path.suffix == ".gz":
            raw = gzip.decompress(raw)
        elif path.suffix == ".pdf":
            from pypdf import PdfReader
            pdf = PdfReader(path)
            raw += (str(pdf.metadata) + "\n".join(page.extract_text() or "" for page in pdf.pages)).encode("utf-8")
        for label, pattern in patterns.items():
            if re.search(pattern, raw):
                raise ValueError(f"Privacy check ({label}) failed: {entry['path']}")
    print("Common credential and local-path checks passed", flush=True)


def main():
    manifest = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    records = manifest["files"]
    seen = set()
    for entry in records:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or entry["path"] in seen:
            raise ValueError(f"Unsafe or duplicate manifest entry: {relative}")
        seen.add(entry["path"])
        path = ROOT / relative
        raw = path.read_bytes()
        if len(raw) != entry["size_bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"Frozen file differs from MANIFEST.json: {relative}")
    print(f"SHA-256 verified: {len(records)} files", flush=True)
    check_privacy(records)
    from package_release import inventory, production_counts, validate_artifacts
    current, _ = inventory(ROOT)
    if {x["path"] for x in current} != seen:
        raise ValueError("Release file inventory differs from MANIFEST.json")
    validate_artifacts(ROOT, current)
    print(f"Production log counts verified: {production_counts(ROOT)['total']:,}", flush=True)
    with tempfile.TemporaryDirectory(prefix="worlds2026-check-") as temporary:
        copy = Path(temporary) / ROOT.name
        copy.mkdir()
        for entry in records:
            source = ROOT / entry["path"]
            destination = copy / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for arguments in (["src/verify_outputs.py"], ["src/update_state_example.py", "--n", "200"]):
            subprocess.run([sys.executable, *arguments], cwd=copy, check=True)
    print("Repository checks passed; frozen working files were not modified.", flush=True)


if __name__ == "__main__":
    main()
