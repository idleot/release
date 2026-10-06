#!/usr/bin/env python3
"""Assemble the IdleOT CDN bundle tree from producer slices.

Inputs (directories extracted from images; any may be missing):
  --previous  tree of the last ghcr.io/idleot/cdn image (/usr/share/nginx/html)
  --wasm      otclient-wasm slice   (client/<rev>/wasm/*, .idleot/wasm.json)
  --desktop   otclient-desktop slice (desktop/<rev>/{installers,files/}, .idleot/{desktop,update}.json)
  --assets    cdn-assets slice      (assets/{things,store,outfits,items}/*, .idleot/assets.json)
  --launcher  launcher slice        (launcher/<version>/*, .idleot/launcher.json)

Output tree (every manifest URL is relative to the bundle root, except
launcher/latest.json — the Tauri updater needs absolute URLs, see --cdn-url):
  latest/client-version.json
  latest/desktop.json          installers per platform (the launcher's once it is published)
  launcher/latest.json         Tauri updater manifest (signed artifacts in launcher/<version>/)
  launcher/<version>/...       last --keep-launcher versions
  latest/update.json           in-client updater manifest (desktop/<rev>/files/)
  latest/sources.json          pointers + revision history (drives pruning)
  latest/release.json          v3 launcher / client-sync manifest (targets -> releases/<rev>/*.json)
  client/<rev>/wasm/...        last --keep-wasm revisions
  desktop/<rev>/...            last --keep-desktop revisions
  releases/<rev>/*.json        v3 file lists, last --keep-releases revisions (+ any still in latest)
  content/<aa>/<sha256>        blobs referenced by the kept releases/ lists
  assets/{things,store,outfits,items}/...
  healthz

v3 lists come from <slice>/.idleot/v3/<name>.json with blobs in <slice>/content/:
  content-desktop / content-web  -> targets.desktop / targets.web   {engine, content}
  engine-<platform>              -> targets.<platform>              {engine, files}
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


def blob(out: Path, digest: str) -> Path:
    return out / "content" / digest[:2] / digest


def import_v3(src: Path, out: Path, targets: dict) -> list[str]:
    """Copy a slice's v3 lists + blobs into the bundle and point targets at them; returns revisions."""
    lists = sorted((src / ".idleot" / "v3").glob("*.json"))
    if not lists:
        return []
    copy_tree(src / "content", out / "content")
    revisions: list[str] = []
    for path in lists:
        doc = json.loads(path.read_text())
        rev, name = doc["revision"], path.stem
        rel = f"releases/{rev}/{name}.json"
        write_json(out / rel, doc)
        if name.startswith("content-"):
            targets[name.removeprefix("content-")] = {"engine": doc["engine"], "content": rel}
        elif name.startswith("engine-"):
            targets[name.removeprefix("engine-")] = {"engine": doc["engine"], "files": rel}
        else:
            print(f"[assemble] warning: unknown v3 list {path.name}", file=sys.stderr)
            continue
        if rev not in revisions:
            revisions.append(rev)
    return revisions


def list_paths(target: dict) -> str:
    return target.get("content") or target.get("files") or ""


def finish_v3(out: Path, release: dict, history: list[str]) -> None:
    """Drop broken targets, prune releases/ and content/, write latest/release.json."""
    targets = release["targets"]
    for name, target in list(targets.items()):
        doc = load_json(out / list_paths(target))
        missing = None if doc is None else sum(
            1 for f in doc.get("files", {}).values() if not blob(out, f["sha256"]).is_file())
        if missing is None or missing:
            print(f"[assemble] warning: v3 target {name} incomplete ({list_paths(target)}) — dropped",
                  file=sys.stderr)
            del targets[name]

    keep = set(history) | {list_paths(t).split("/")[1] for t in targets.values()}
    prune(out / "releases", sorted(keep))

    referenced: set[str] = set()
    for path in (out / "releases").rglob("*.json") if (out / "releases").is_dir() else []:
        referenced.update(f["sha256"] for f in json.loads(path.read_text()).get("files", {}).values())
    pruned = 0
    for path in (out / "content").rglob("*") if (out / "content").is_dir() else []:
        if path.is_file() and path.name not in referenced:
            path.unlink()
            pruned += 1
    if pruned:
        print(f"[assemble] pruned {pruned} unreferenced blobs")

    if targets:
        write_json(out / "latest" / "release.json", release)
    else:
        print("[assemble] warning: no v3 targets — latest/release.json omitted", file=sys.stderr)


# Release target -> tauri-plugin-updater platform key.
TAURI_PLATFORMS = {"windows-x64": "windows-x86_64", "macos-arm64": "darwin-aarch64", "linux-x64": "linux-x86_64"}


def launcher_manifests(out: Path, launcher: dict, cdn_url: str) -> dict | None:
    """Write launcher/latest.json for the published launcher; returns its desktop.json, or None if incomplete."""
    files = [p["url"] for p in launcher.get("platforms", {}).values()]
    files += [u["url"] for u in launcher.get("updater", {}).values()]
    if not files or not all((out / f).is_file() for f in files):
        print(f"[assemble] warning: launcher {launcher.get('version')} files missing — not published",
              file=sys.stderr)
        return None
    write_json(out / "launcher" / "latest.json", {
        "version": launcher["version"],
        "notes": launcher.get("notes", ""),
        "pub_date": launcher.get("publishedAt", ""),
        "platforms": {
            TAURI_PLATFORMS[name]: {"signature": u["signature"], "url": f"{cdn_url}/{u['url']}"}
            for name, u in launcher["updater"].items() if name in TAURI_PLATFORMS
        },
    })
    return {
        "schema": 2,
        "kind": "launcher",
        "revision": launcher["version"],
        "version": launcher["version"],
        "productSha": launcher.get("productSha", ""),
        "publishedAt": launcher.get("publishedAt", ""),
        "platforms": launcher["platforms"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--previous")
    ap.add_argument("--wasm")
    ap.add_argument("--desktop")
    ap.add_argument("--assets")
    ap.add_argument("--launcher")
    ap.add_argument("--cdn-url", default="https://cdn.idleot.com",
                    help="public bundle root (absolute URLs in launcher/latest.json)")
    ap.add_argument("--keep-wasm", type=int, default=3)
    ap.add_argument("--keep-desktop", type=int, default=2)
    ap.add_argument("--keep-releases", type=int, default=3)
    ap.add_argument("--keep-launcher", type=int, default=2)
    args = ap.parse_args()
    cdn_url = args.cdn_url.rstrip("/")

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    previous = slice_dir(args.previous)
    wasm = slice_dir(args.wasm)
    desktop = slice_dir(args.desktop)
    assets = slice_dir(args.assets)
    launcher_slice = slice_dir(args.launcher)

    prev_sources = (load_json(previous / "latest" / "sources.json") if previous else None) or {}
    sources: dict = {
        "wasm": prev_sources.get("wasm"),
        "desktop": prev_sources.get("desktop"),
        "update": prev_sources.get("update"),
        "assets": prev_sources.get("assets"),
        "launcher": prev_sources.get("launcher"),
        "history": prev_sources.get("history") or {"wasm": [], "desktop": []},
    }

    if previous:
        for name in ("client", "desktop", "assets", "releases", "content", "launcher"):
            copy_tree(previous / name, out / name)

    prev_release = (load_json(previous / "latest" / "release.json") if previous else None) or {}
    release: dict = {
        "schema": 3,
        "revision": prev_release.get("revision", ""),
        "targets": dict(prev_release.get("targets") or {}),
    }
    if prev_release.get("launcher"):
        release["launcher"] = prev_release["launcher"]
    new_revisions = [r for s in (wasm, desktop) if s for r in import_v3(s, out, release["targets"])]
    if new_revisions:
        # Desktop revisions are dated (YYYYMMDD-sha); WASM ones are content hashes.
        lead = release["targets"].get("desktop") or release["targets"].get("web") or {}
        release["revision"] = list_paths(lead).split("/")[1] if list_paths(lead) else new_revisions[0]

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
    if launcher_slice:
        copy_tree(launcher_slice / "launcher", out / "launcher")
        sources["launcher"] = load_json(launcher_slice / ".idleot" / "launcher.json") or sources["launcher"]

    history = sources["history"]
    history["wasm"] = remember(history.get("wasm", []), (sources["wasm"] or {}).get("revision"), args.keep_wasm)
    history["desktop"] = remember(
        history.get("desktop", []), (sources["desktop"] or {}).get("revision"), args.keep_desktop
    )
    releases = history.get("releases", [])
    for rev in sorted(new_revisions):
        releases = remember(releases, rev, args.keep_releases)
    history["releases"] = releases
    history["launcher"] = remember(
        history.get("launcher", []), (sources["launcher"] or {}).get("version"), args.keep_launcher
    )
    prune(out / "client", history["wasm"])
    prune(out / "desktop", history["desktop"])
    prune(out / "launcher", history["launcher"])

    (out / "launcher" / "latest.json").unlink(missing_ok=True)
    launcher_desktop = launcher_manifests(out, sources["launcher"], cdn_url) if sources["launcher"] else None
    if launcher_desktop:
        release["launcher"] = {"version": launcher_desktop["version"]}
    else:
        release.pop("launcher", None)
    finish_v3(out, release, history["releases"])

    w = sources["wasm"]
    if w and (out / "client" / w["revision"] / "wasm" / "otclient.js").is_file():
        rev = w["revision"]
        wasm_files = {
            "js": f"client/{rev}/wasm/otclient.js",
            "wasm": f"client/{rev}/wasm/otclient.wasm",
        }
        # Builds before the v3 content store bake the runtime into otclient.data.
        if (out / "client" / rev / "wasm" / "otclient.data").is_file():
            wasm_files["data"] = f"client/{rev}/wasm/otclient.data"
        manifest = {
            "revision": rev,
            "productSha": w.get("productSha", ""),
            "assetsRevision": (sources["assets"] or {}).get("assetsRevision", ""),
            "wasm": wasm_files,
        }
        web = release["targets"].get("web")
        if web and web.get("content") == f"releases/{rev}/content-web.json":
            manifest["content"] = web["content"]
        elif "data" not in wasm_files:
            print(f"[assemble] warning: WASM {rev} has neither otclient.data nor content-web", file=sys.stderr)
        write_json(out / "latest" / "client-version.json", manifest)
    else:
        print("[assemble] warning: no WASM slice — latest/client-version.json omitted", file=sys.stderr)

    d = sources["desktop"]
    if launcher_desktop:
        write_json(out / "latest" / "desktop.json", launcher_desktop)
    elif d and all((out / p["url"]).is_file() for p in d.get("platforms", {}).values()):
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
