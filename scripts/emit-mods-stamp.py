#!/usr/bin/env python3
"""Emit tip mods.json.sha256 (+ mods.json) for one or more staged packs.

qtclient's launcher mods channel compares mods.json.sha256 before Play.
Accepts either:
  - a single pack dir (…/mods/<id> with mod.json), or
  - a tip mods/ root containing multiple <id>/mod.json trees (multi-pack).

files[].localfile always uses mods/<id>/… so installMods can stage N packs.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _pack_dirs(arg: Path) -> list[Path]:
    if (arg / "mod.json").is_file():
        return [arg]
    found = sorted(
        p for p in arg.iterdir() if p.is_dir() and (p / "mod.json").is_file() and not p.name.startswith("_")
    )
    if not found:
        raise SystemExit(f"no pack dirs with mod.json under {arg}")
    return found


def _hash_pack(pack: Path) -> tuple[str, list[dict], dict]:
    files: list[dict] = []
    h = hashlib.sha256()
    for path in sorted(p for p in pack.rglob("*") if p.is_file()):
        # Skip build leftovers if someone pointed at a staged tree with lib/
        rel = path.relative_to(pack).as_posix()
        if rel.startswith("lib/") or rel.startswith("native/"):
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        h.update(rel.encode())
        h.update(b"\0")
        h.update(digest.encode())
        h.update(b"\0")
        files.append(
            {
                "url": f"{pack.name}/{rel}",
                "localfile": f"mods/{pack.name}/{rel}",
                "unpackedhash": digest,
                "unpackedsize": len(data),
                "packedhash": digest,
                "packedsize": len(data),
            }
        )
    mod_doc = json.loads((pack / "mod.json").read_text(encoding="utf-8"))
    meta = {
        "id": mod_doc.get("id") or pack.name,
        "priority": int(mod_doc.get("priority") or 100),
        "hostBuildId": mod_doc.get("hostBuildId") or "",
        "contentHash": h.hexdigest(),
    }
    return h.hexdigest(), files, meta


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"usage: {sys.argv[0]} <mods/<id> | tip/mods> <tip package root>",
            file=sys.stderr,
        )
        return 2
    src = Path(sys.argv[1]).resolve()
    tip = Path(sys.argv[2]).resolve()
    if not src.is_dir():
        print(f"missing {src}", file=sys.stderr)
        return 1

    packs = _pack_dirs(src)
    # Branded tip still needs at least one brand.json among packs (launcher chrome).
    if not any((p / "brand.json").is_file() for p in packs):
        print(
            f"missing brand.json under {src} (required for branded tip)",
            file=sys.stderr,
        )
        return 1

    all_files: list[dict] = []
    pack_metas: list[dict] = []
    tip_hash = hashlib.sha256()
    for pack in packs:
        _ph, files, meta = _hash_pack(pack)
        tip_hash.update(meta["id"].encode())
        tip_hash.update(b"\0")
        tip_hash.update(meta["contentHash"].encode())
        tip_hash.update(b"\0")
        all_files.extend(files)
        pack_metas.append(
            {
                "id": meta["id"],
                "priority": meta["priority"],
                "hostBuildId": meta["hostBuildId"],
                "contentHash": meta["contentHash"],
            }
        )

    pack_metas.sort(key=lambda m: (m["priority"], m["id"]))
    stamp = tip_hash.hexdigest()
    primary = pack_metas[0]
    doc = {
        "version": stamp,
        "id": primary["id"],
        "hostBuildId": primary.get("hostBuildId") or "",
        "packs": pack_metas,
        "files": all_files,
    }
    (tip / "mods.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (tip / "mods.json.sha256").write_text(stamp + "\n", encoding="utf-8")
    ids = ",".join(m["id"] for m in pack_metas)
    print(
        f"[emit-mods-stamp] [{ids}] → {tip}/mods.json.sha256 "
        f"({stamp[:12]}…, {len(all_files)} files, {len(pack_metas)} packs)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
