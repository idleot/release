#!/usr/bin/env python3
"""Emit tip minimap.json + minimap.json.sha256 for Contents/Resources/minimap.

qtclient's launcher minimap channel (gated by launcher mod capability "minimap")
compares minimap.json.sha256 before Play.

Accepts a minimap tree (tiles under the given dir). files[].localfile always uses
Contents/Resources/minimap/… so installMinimap stages next to CIP assets.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"usage: {sys.argv[0]} <minimap dir> <tip package root>",
            file=sys.stderr,
        )
        return 2
    src = Path(sys.argv[1]).resolve()
    tip = Path(sys.argv[2]).resolve()
    if not src.is_dir():
        print(f"missing {src}", file=sys.stderr)
        return 1
    tip.mkdir(parents=True, exist_ok=True)

    files: list[dict] = []
    h = hashlib.sha256()
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = path.relative_to(src).as_posix()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        h.update(rel.encode())
        h.update(b"\0")
        h.update(digest.encode())
        h.update(b"\0")
        files.append(
            {
                "url": rel,
                "localfile": f"Contents/Resources/minimap/{rel}",
                "unpackedhash": digest,
                "unpackedsize": len(data),
                "packedhash": digest,
                "packedsize": len(data),
            }
        )
    content_hash = h.hexdigest()
    doc = {"files": files, "contentHash": content_hash}
    (tip / "minimap.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (tip / "minimap.json.sha256").write_text(content_hash + "\n", encoding="utf-8")
    print(f"emit-minimap-stamp: {len(files)} files → {tip}/minimap.json.sha256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
