"""Update one guide in config/guides.manifest.json in one line.

    python -m hub.guide_manifest update <guide-id> <new.pdf> --version "rev 8, Nov 2026" --approval "Mark approved rev 8 FINAL <date> (...)"

Copies the PDF into send_guide.guides_dir (from config\\integrations.json, else
the default), works out its SHA-256, and rewrites that one entry's path,
version, sha256 and approval_ref. Nothing else changes. Then commit the
manifest. An --approval reference is required: an unapproved file never goes in.

    python -m hub.guide_manifest list      (what is in the manifest, and whether each file still matches)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from hub import integrations, sendguide


def _cfg() -> dict:
    base = Path(__file__).resolve().parent.parent / "config"
    path = Path(os.getenv("CEC_HUB_INTEGRATIONS") or base / "integrations.json")
    return integrations.load_integrations(path) if path.is_file() else {}


def update(cfg: dict, gid: str, pdf: Path, version: str, approval: str) -> dict:
    mpath = sendguide.manifest_path(cfg)
    data = json.loads(mpath.read_text(encoding="utf-8"))
    entry = next((g for g in data.get("guides", []) if g.get("id") == gid), None)
    if entry is None:
        raise SystemExit(f"No guide with id {gid!r} in {mpath}")
    if not approval.strip():
        raise SystemExit("--approval is required")
    if not sendguide.SAFE_FILE_RE.match(pdf.name):
        raise SystemExit("File name must be plain letters/numbers and end .pdf")
    raw = pdf.read_bytes()
    if not raw.startswith(b"%PDF-"):
        raise SystemExit("That isn't a PDF")
    dest_dir = sendguide.guides_dir(cfg)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / pdf.name
    if pdf.resolve() != dest.resolve():
        shutil.copyfile(pdf, dest)
    entry.update(path=pdf.name, version=version, sha256=hashlib.sha256(raw).hexdigest(),
                 approval_ref=approval)
    mpath.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m hub.guide_manifest")
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update")
    u.add_argument("guide_id")
    u.add_argument("pdf", type=Path)
    u.add_argument("--version", required=True)
    u.add_argument("--approval", required=True)
    sub.add_parser("list")
    a = p.parse_args(argv)
    cfg = _cfg()
    if a.cmd == "update":
        e = update(cfg, a.guide_id, a.pdf, a.version, a.approval)
        print(f"{e['id']}: {e['path']} {e['version']} sha256 {e['sha256']}")
    else:
        for g in sendguide.guides_for_page(cfg):
            print(f"{g['id']:24} {g['language']:4} {g['version']:18} {'OK' if g['ok'] else g['problem']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
