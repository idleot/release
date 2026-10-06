#!/usr/bin/env python3
"""Assemble the IdleOT CDN bundle tree from producer slices.

Inputs (directories extracted from images; any may be missing):
  --previous  tree of the last ghcr.io/idleot/cdn image (/usr/share/nginx/html)
  --wasm      otclient-wasm slice   (client/<rev>/wasm/*, .idleot/wasm.json)
  --desktop   otclient-desktop slice (desktop/<rev>/{installers,files/}, .idleot/{desktop,update}.json)
  --assets    cdn-assets slice      (assets/things/*, assets/store/*, .idleot/assets.json)

Output tree (every manifest URL is relative to the bundle root):
  latest/client-version.json
  latest/desktop.json          installers per platform
  latest/update.json           in-client updater manifest (desktop/<rev>/files/)
  latest/sources.json          pointers + revision history (drives pruning)
  client/<rev>/wasm/...        last --keep-wasm revisions
  desktop/<rev>/...            last --keep-desktop revisions
  assets/things/..., assets/store/...
  healthz
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def copy_tree(src: Path, dest: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)


def slice_dir(arg: str | None) -> Path | None:
    if not arg:
        return None
    p = Path(arg)
    return p if p.is_dir() and any(p.iterdir()) else None


def remember(history: list[str], rev: str | None, keep: int) -> list[str]:
    """Most-recent-first revision list capped at `keep`."""
    if rev:
        history = [rev] + [r for r in history if r != rev]
    return history[:keep]


def prune(root: Path, keep: list[str]) -> None:
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and child.name not in keep:
            print(f"[assemble] pruning {child}")
            shutil.rmtree(child)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--previous")
    ap.add_argument("--wasm")
    ap.add_argument("--desktop")
    ap.add_argument("--assets")
    ap.add_argument("--keep-wasm", type=int, default=3)
    ap.add_argument("--keep-desktop", type=int, default=2)
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    previous = slice_dir(args.previous)
    wasm = slice_dir(args.wasm)
    desktop = slice_dir(args.desktop)
    assets = slice_dir(args.assets)

    prev_sources = (load_json(previous / "latest" / "sources.json") if previous else None) or {}
    sources: dict = {
        "wasm": prev_sources.get("wasm"),
        "desktop": prev_sources.get("desktop"),
        "update": prev_sources.get("update"),
        "assets": prev_sources.get("assets"),
        "history": prev_sources.get("history") or {"wasm": [], "desktop": []},
    }

    if previous:
        for name in ("client", "desktop", "assets"):
            copy_tree(previous / name, out / name)

    if wasm:
        copy_tree(wasm / "client", out / "client")
        sources["wasm"] = load_json(wasm / ".idleot" / "wasm.json") or sources["wasm"]
    if desktop:
        copy_tree(desktop / "desktop", out / "desktop")
        sources["desktop"] = load_json(desktop / ".idleot" / "desktop.json") or sources["desktop"]
    update = load_json(desktop / ".idleot" / "update.json") if desktop else None
    if update is None and previous:
        update = load_json(previous / "latest" / "update.json")
    if update:
        write_json(out / "latest" / "update.json", update)
        sources["update"] = {"revision": update.get("revision"), "engine": update.get("engine")}
    if assets:
        # The assets slice is authoritative for assets/ (no stale things zips).
        shutil.rmtree(out / "assets", ignore_errors=True)
        copy_tree(assets / "assets", out / "assets")
        sources["assets"] = load_json(assets / ".idleot" / "assets.json") or sources["assets"]

    history = sources["history"]
    history["wasm"] = remember(history.get("wasm", []), (sources["wasm"] or {}).get("revision"), args.keep_wasm)
    history["desktop"] = remember(
        history.get("desktop", []), (sources["desktop"] or {}).get("revision"), args.keep_desktop
    )
    prune(out / "client", history["wasm"])
    prune(out / "desktop", history["desktop"])

    w = sources["wasm"]
    if w and (out / "client" / w["revision"] / "wasm" / "otclient.js").is_file():
        rev = w["revision"]
        write_json(out / "latest" / "client-version.json", {
            "revision": rev,
            "productSha": w.get("productSha", ""),
            "assetsRevision": (sources["assets"] or {}).get("assetsRevision", ""),
            "wasm": {
                "js": f"client/{rev}/wasm/otclient.js",
                "wasm": f"client/{rev}/wasm/otclient.wasm",
                "data": f"client/{rev}/wasm/otclient.data",
            },
        })
    else:
        print("[assemble] warning: no WASM slice — latest/client-version.json omitted", file=sys.stderr)

    d = sources["desktop"]
    if d and all((out / p["url"]).is_file() for p in d.get("platforms", {}).values()):
        write_json(out / "latest" / "desktop.json", {
            "schema": d.get("schema", 1),
            "revision": d["revision"],
            "engine": d.get("engine", ""),
            "productSha": d.get("productSha", ""),
            "publishedAt": d.get("publishedAt", ""),
            "platforms": d.get("platforms", {}),
        })
    else:
        print("[assemble] warning: no desktop slice — latest/desktop.json omitted", file=sys.stderr)

    u = load_json(out / "latest" / "update.json")
    if u and not (out / u.get("url", "")).is_dir():
        # Pruned or missing tree: clients would 404 on every file; drop the manifest.
        print(f"[assemble] warning: {u.get('url')} missing — latest/update.json omitted", file=sys.stderr)
        (out / "latest" / "update.json").unlink()

    if not (out / "assets" / "things" / "manifest.json").is_file():
        print("[assemble] warning: no assets slice — assets/things/manifest.json missing", file=sys.stderr)

    write_json(out / "latest" / "sources.json", sources)
    (out / "healthz").write_text("ok\n")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"[assemble] bundle {total / 1e6:.1f} MB → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
