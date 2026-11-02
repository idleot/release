#!/usr/bin/env python3
"""Emit tip mods.json.sha256 (+ lightweight mods.json) next to a staged pack.

Soft's launcher optional mods channel compares mods.json.sha256 before Play.
This is the bake-into-tip stamp (content hash of mods/<id>/). Full content-
addressed modsurl lzma trees can replace this later without Soft changes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <mods/<id> dir> <tip package root>", file=sys.stderr)
        return 2
    pack = Path(sys.argv[1]).resolve()
    tip = Path(sys.argv[2]).resolve()
    if not pack.is_dir():
        print(f"missing pack dir {pack}", file=sys.stderr)
        return 1
    brand = pack / "brand.json"
    if not brand.is_file():
        print(f"missing {brand} (required for branded tip)", file=sys.stderr)
        return 1
    mod = pack / "mod.json"
    if not mod.is_file():
        print(f"missing {mod}", file=sys.stderr)
        return 1

    files: list[dict[str, str | int]] = []
    h = hashlib.sha256()
    for path in sorted(p for p in pack.rglob("*") if p.is_file()):
        rel = path.relative_to(pack).as_posix()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        h.update(rel.encode())
        h.update(b"\0")
        h.update(digest.encode())
        h.update(b"\0")
        files.append(
            {
                "url": rel,
                "localfile": f"mods/{pack.name}/{rel}",
                "unpackedhash": digest,
                "unpackedsize": len(data),
                "packedhash": digest,
                "packedsize": len(data),
            }
        )
    stamp = h.hexdigest()
    mod_doc = json.loads(mod.read_text(encoding="utf-8"))
    doc = {
        "version": stamp,
        "id": mod_doc.get("id") or pack.name,
        "hostBuildId": mod_doc.get("hostBuildId") or "",
        "files": files,
    }
    (tip / "mods.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (tip / "mods.json.sha256").write_text(stamp + "\n", encoding="utf-8")
    print(f"[emit-mods-stamp] {pack.name} → {tip}/mods.json.sha256 ({stamp[:12]}…, {len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
