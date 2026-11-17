#!/usr/bin/env python3
"""Delete content/ blobs and packs/ zips on the CDN host that nothing references.

Runs on the host (sync-cdn.sh flip pipes it over ssh), after latest/ and
releases/ are flipped. assemble.py prunes the same way, but only in the CI
workspace; the host store is pushed with --ignore-existing and grew forever.

Kept, whatever else happens:
  - every "sha256" / "packedhash" value in any JSON under releases/ (v3 lists,
    their packs) and launcher/ (CipSoft package.json, assets*.json,
    minimap.json: payloads the launcher can fetch from content/<packedhash>);
  - packs/<sha256>.zip for any of those hashes;
  - any file whose ctime is inside the grace window: blobs a bundle run pushed
    for a release that has not flipped yet. ctime, not mtime: rsync -a keeps
    the source mtime, which can be months old for a reused blob.

Refuses to delete anything when releases/ has no lists, or when a list that
latest/ or previous-latest/ points at is missing (a broken flip).

Usage: gc-cdn-host.py <cdn root> [grace hours, default 48] [--dry-run]
       gc-cdn-host.py --outgoing-lists <cdn root>
         (print the host releases/<dir> paths previous-latest/release.json
         names; sync-cdn.sh flip keeps them so the rollback snapshot resolves)
<cdn root> holds html/ and previous-latest/.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HASH_KEYS = ("sha256", "packedhash")


def walk(node, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in HASH_KEYS and isinstance(value, str):
                found.add(value)
            else:
                walk(value, found)
    elif isinstance(node, list):
        for value in node:
            walk(value, found)


def list_refs(node, found: set[str]) -> None:
    """Every "releases/….json" string a manifest names."""
    if isinstance(node, dict):
        for value in node.values():
            list_refs(value, found)
    elif isinstance(node, list):
        for value in node:
            list_refs(value, found)
    elif isinstance(node, str) and node.startswith("releases/") and node.endswith(".json"):
        found.add(node)


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        sys.exit(f"[gc-cdn] refusing: cannot read {path}: {error}")


def outgoing_lists(root: Path) -> int:
    manifest = root / "previous-latest" / "release.json"
    if not manifest.is_file():
        return 0
    named: set[str] = set()
    list_refs(load(manifest), named)
    for rel in sorted({"/".join(rel.split("/")[:2]) for rel in named}):
        if (root / "html" / rel).is_dir():
            print(root / "html" / rel)
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--outgoing-lists"]:
        return outgoing_lists(Path(sys.argv[2]))
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv[1:]
    root = Path(args[0])
    grace_hours = float(args[1]) if len(args) > 1 else 48.0
    html = root / "html"

    lists = sorted((html / "releases").rglob("*.json")) if (html / "releases").is_dir() else []
    if not lists:
        print("[gc-cdn] refusing: no release lists on the host")
        return 1
    for manifest in (html / "latest" / "release.json", root / "previous-latest" / "release.json"):
        if not manifest.is_file():
            continue
        named: set[str] = set()
        list_refs(load(manifest), named)
        missing = sorted(rel for rel in named if not (html / rel).is_file())
        if missing:
            print(f"[gc-cdn] refusing: {manifest} names missing lists: {', '.join(missing)}")
            return 1

    keep: set[str] = set()
    for path in lists:
        walk(load(path), keep)
    for path in sorted((html / "launcher").rglob("*.json")) if (html / "launcher").is_dir() else []:
        walk(load(path), keep)

    cutoff = time.time() - grace_hours * 3600
    for kind, base, name_of in (
        ("blobs", html / "content", lambda p: p.name),
        ("packs", html / "packs", lambda p: p.name.removesuffix(".zip")),
    ):
        count = size = recent = 0
        for path in base.rglob("*") if base.is_dir() else []:
            if not path.is_file() or name_of(path) in keep:
                continue
            stat = path.stat()
            if stat.st_ctime >= cutoff:
                recent += 1
                continue
            count += 1
            size += stat.st_size
            if not dry_run:
                path.unlink()
        verb = "would prune" if dry_run else "pruned"
        print(f"[gc-cdn] {verb} {count} unreferenced {kind} ({size / 2**30:.2f} GiB); "
              f"kept {recent} recent unreferenced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
