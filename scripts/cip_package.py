#!/usr/bin/env python3
"""Write CipSoft launcher paths into a CDN bundle.

The host is ours. The path after it matches static.tibia.com:

  /launcher/tibiametadata.json
  /launcher/launcher-{windows,macos,linux,web}-current/package.json
  /launcher/launcher-*-current/package.json.version
  /launcher/tibiaclient-*-current/package.json
  /launcher/tibiaclient-*-current/package.json.version
  /launcher/assets-current/assets.json
  /launcher/assets-current/assets-macos.json
  /launcher/assets-current/assets.json.sha256
  /hints.json

File records use CIP's names. Payloads are lzma-alone, content-addressed under
content/<packedhash>, and hardlinked at <localfile>.lzma inside the package
directory (the url the launcher requests).
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

OTC = Path(__file__).resolve().parents[2] / "otclient"

# Tip packages[].name + directoryname → packages/<name>/ on disk.
# Soft uses "Tibia" / "Tibia.app"; IdleOT overrides both to this value.
CLIENT_PACKAGE_DIR = "IdleOT"

# (os, architecture field, v3 engine target or "web", launcher dir, client dir)
PLATFORMS = (
    ("windows", "x86_64", "windows-x64", "launcher-windows-current", "tibiaclient-windows-current"),
    ("macos", "x86_64;arm64", "macos-arm64", "launcher-macos-current", "tibiaclient-macos-current"),
    ("linux", "x86_64", "linux-x64", "launcher-linux-current", "tibiaclient-linux-current"),
    ("web", "wasm", "web", "launcher-web-current", "tibiaclient-web-current"),
)

DESKTOP_JSON_KEYS = {
    "windows-x64": "windows-x64",
    "macos-arm64": "macos-arm64",
    "linux-x64": "linux-x64",
}

# CIP os name → installers/<platform>/ from the desktop slice.
INSTALLER_PLATFORM = {
    "windows": "windows-x64",
    "macos": "macos-arm64",
    "linux": "linux-x64",
}

LAUNCHER_EXECUTABLE = {
    "windows": "IdleOT.exe",
    "macos": "IdleOT.app/Contents/MacOS/IdleOT",
    "linux": "idleot-launcher",
}

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def _unpacked_map_from_assets(assets_json: Path) -> dict[str, dict]:
    """Map unpackedhash → prior CIP file record (skip re-lzma when tip reused)."""
    doc = load_json(assets_json) or {}
    out: dict[str, dict] = {}
    for rec in doc.get("files") or []:
        uh = rec.get("unpackedhash")
        if uh:
            out[uh] = rec
    return out


def pack_file(
    out: Path,
    package_dir: Path,
    blob: Path,
    localfile: str,
    *,
    prior_by_unpacked: dict[str, dict] | None = None,
) -> dict | None:
    """lzma-compress one unpacked blob. Returns a CIP file record, or None if missing."""
    if not blob.is_file():
        return None
    data = blob.read_bytes()
    unpacked_hash = sha256_bytes(data)
    prior = (prior_by_unpacked or {}).get(unpacked_hash)
    if prior and prior.get("packedhash"):
        packed_hash = prior["packedhash"]
        canon = out / "content" / packed_hash[:2] / packed_hash
        if canon.is_file():
            rel = localfile.lstrip("/") + ".lzma"
            _link_or_copy(canon, package_dir / rel)
            return {
                "url": rel,
                "localfile": localfile.lstrip("/"),
                "packedhash": packed_hash,
                "packedsize": int(prior.get("packedsize") or canon.stat().st_size),
                "unpackedhash": unpacked_hash,
                "unpackedsize": len(data),
            }

    packed = lzma.compress(data, format=lzma.FORMAT_ALONE)
    packed_hash = sha256_bytes(packed)
    canon = out / "content" / packed_hash[:2] / packed_hash
    if not canon.is_file():
        canon.parent.mkdir(parents=True, exist_ok=True)
        canon.write_bytes(packed)
    rel = localfile.lstrip("/") + ".lzma"
    _link_or_copy(canon, package_dir / rel)
    return {
        "url": rel,
        "localfile": localfile.lstrip("/"),
        "packedhash": packed_hash,
        "packedsize": len(packed),
        "unpackedhash": unpacked_hash,
        "unpackedsize": len(data),
    }


def records_from_list(out: Path, package_dir: Path, list_rel: str | None) -> list[dict]:
    if not list_rel:
        return []
    doc = load_json(out / list_rel)
    if not doc:
        return []
    prior = _unpacked_map_from_assets(out / "launcher" / "assets-current" / "assets.json")
    records: list[dict] = []
    files = doc.get("files", {})
    for index, (path, entry) in enumerate(files.items(), start=1):
        if index == 1 or index % 500 == 0 or index == len(files):
            print(f"[assemble] CIP {package_dir.name} {index}/{len(files)} {path}")
        digest = entry.get("sha256", "")
        blob = out / "content" / digest[:2] / digest
        rec = pack_file(out, package_dir, blob, path.lstrip("/"), prior_by_unpacked=prior)
        if rec:
            records.append(rec)
        else:
            print(f"[assemble] warning: CIP package missing blob {path}")
    return records


def cip_package_version(fallback: str = "0") -> str:
    """CIP Play-button stamp: `<cip_version>.<6 hex>` (e.g. 15.33.c9e45e).

    Content-store revisions stay dated elsewhere; only the launcher-facing
    package.json.version uses this short form so it fits CIP's package chrome.
    """
    cip = "15.33"
    version_json = OTC / "cip" / "VERSION.json"
    if version_json.is_file():
        try:
            cip = json.loads(version_json.read_text()).get("cip_version") or cip
        except (OSError, ValueError):
            pass
    sha = os.environ.get("SOURCE_COMMIT", "").strip()
    if not sha:
        try:
            sha = subprocess.run(
                ["git", "-C", str(OTC), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            # Last dash segment of dated revisions (…-1aa4a8c4 / …-fovodd-1aa4a8c4).
            sha = fallback.rsplit("-", 1)[-1] if fallback else "0"
    return f"{cip}.{sha[:6]}"


def write_package(directory: Path, version: str, executable: str, files: list[dict], generation: str | None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    doc: dict = {"version": version, "files": files, "executable": executable}
    if generation:
        doc["creation_time"] = 0
        doc["generation"] = generation
    write_json(directory / "package.json", doc)
    (directory / "package.json.version").write_text(version)


def executable_of(out: Path, list_rel: str | None, fallback: str) -> str:
    doc = load_json(out / list_rel) if list_rel else None
    entry = (doc or {}).get("entry") or ""
    entry = str(entry).lstrip("/")
    return entry or fallback


def copy_plain(src: Path, dest: Path) -> None:
    if not src.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    shutil.copy2(src, dest)


def pack_tree(out: Path, package_dir: Path, tree: Path) -> list[dict]:
    """lzma-pack every file under tree; localfile paths are relative to tree."""
    if not tree.is_dir():
        return []
    records: list[dict] = []
    files = sorted(p for p in tree.rglob("*") if p.is_file())
    for index, path in enumerate(files, start=1):
        if index == 1 or index % 200 == 0 or index == len(files):
            print(f"[assemble] CIP {package_dir.name} {index}/{len(files)} {path.relative_to(tree)}")
        rec = pack_file(out, package_dir, path, path.relative_to(tree).as_posix())
        if rec:
            records.append(rec)
    return records


def emit_cip(
    out: Path,
    cdn_url: str,
    launcher_version: str,
    launcher_files: list[Path] | None = None,
) -> bool:
    """Write the CIP tree from lists already assembled into `out`. Returns True if metadata was written."""
    release = load_json(out / "latest" / "release.json")
    if not release or not release.get("targets"):
        print("[assemble] warning: no release.json — CIP launcher tree omitted")
        return False
    cdn_url = cdn_url.rstrip("/")
    targets: dict = release["targets"]
    things_rel = next((t.get("things") for t in targets.values() if t.get("things")), None)

    assets_dir = out / "launcher" / "assets-current"
    reuse_marker = out / ".idleot" / "reuse-assets"
    if reuse_marker.is_file() and (assets_dir / "assets.json").is_file():
        # Host/previous tip already has packed assets — do not re-lzma.
        print(f"[assemble] CIP assets-current reused ({reuse_marker.read_text().strip() or 'unchanged'})")
        assets_doc = load_json(assets_dir / "assets.json") or {"files": []}
        if not (assets_dir / "assets-macos.json").is_file():
            write_json(assets_dir / "assets-macos.json", assets_doc)
        if not (assets_dir / "assets.json.sha256").is_file():
            (assets_dir / "assets.json.sha256").write_text(
                sha256_bytes((assets_dir / "assets.json").read_bytes())
            )
    else:
        asset_files = records_from_list(out, assets_dir, things_rel)
        assets_doc = {"files": asset_files}
        write_json(assets_dir / "assets.json", assets_doc)
        assets_bytes = (assets_dir / "assets.json").read_bytes()
        (assets_dir / "assets.json.sha256").write_text(sha256_bytes(assets_bytes))
        write_json(assets_dir / "assets-macos.json", assets_doc)

    # CIP shows 15.33.c9e45e under Play — not a long dated/branch stamp.
    version = cip_package_version(release.get("revision") or "0")
    launcher_version = cip_package_version(launcher_version)
    launcher_builds = []
    packages_builds = []
    for os_name, arch, target_name, launcher_dir_name, client_dir_name in PLATFORMS:
        launcher_dir = out / "launcher" / launcher_dir_name
        client_dir = out / "launcher" / client_dir_name
        launcher_records: list[dict] = []
        launcher_exe = "launcher"
        if os_name == "web":
            wasm_rel = (targets.get("web") or {}).get("wasm")
            wasm_dir = out / wasm_rel if wasm_rel else None
            if wasm_dir and wasm_dir.is_dir():
                for name in ("launcher.js", "launcher.wasm", "qtloader.js"):
                    src = wasm_dir / name
                    copy_plain(src, launcher_dir / name)
                    rec = pack_file(out, launcher_dir, src, name)
                    if rec:
                        launcher_records.append(rec)
        else:
            plat = INSTALLER_PLATFORM[os_name]
            tree = out / "installers" / plat
            if tree.is_dir():
                launcher_records.extend(pack_tree(out, launcher_dir, tree))
                launcher_exe = LAUNCHER_EXECUTABLE[os_name]
            else:
                # Legacy Tauri installers dropped at launcher/*.dmg|exe|appimage|zip.
                for path in launcher_files or []:
                    if path.is_file() and plat.replace("-", "") in path.name.replace("-", "").lower():
                        rec = pack_file(out, launcher_dir, path, path.name)
                        if rec:
                            launcher_records.append(rec)
                if not launcher_records:
                    for path in launcher_files or []:
                        if path.is_file():
                            rec = pack_file(out, launcher_dir, path, path.name)
                            if rec:
                                launcher_records.append(rec)
            if not launcher_records:
                print(f"[assemble] warning: no desktop launcher files for {plat}")
        write_package(launcher_dir, launcher_version, launcher_exe, launcher_records, None)
        launcher_builds.append({
            "os": os_name,
            "architecture": arch,
            "url": f"{cdn_url}/launcher/{launcher_dir_name}/package.json",
            "versionurl": f"{cdn_url}/launcher/{launcher_dir_name}/package.json.version",
        })

        target = targets.get(target_name) or {}
        files: list[dict] = []
        if os_name == "web":
            files.extend(records_from_list(out, client_dir, target.get("content")))
            wasm_rel = target.get("wasm")
            wasm_dir = out / wasm_rel if wasm_rel else None
            if wasm_dir and wasm_dir.is_dir():
                # tibia.data is the Emscripten preload sidecar; qtloader fetches
                # it next to tibia.js when /play boots the game.
                required_wasm = {"tibia.js", "tibia.wasm"}
                for name in ("tibia.js", "tibia.wasm", "tibia.data", "qtloader.js"):
                    src = wasm_dir / name
                    if not src.is_file():
                        if name in required_wasm:
                            raise SystemExit(
                                f"[assemble] missing required {src} — refusing to ship a broken /play package"
                            )
                        if name == "tibia.data":
                            print(f"[assemble] warning: missing {src} — /play will 404 tibia.data")
                        continue
                    copy_plain(src, client_dir / name)
                    rec = pack_file(out, client_dir, src, name)
                    if rec:
                        files.append(rec)
            exe = "tibia.js"
        else:
            files.extend(records_from_list(out, client_dir, target.get("files")))
            files.extend(records_from_list(out, client_dir, target.get("content")))
            exe = executable_of(out, target.get("files"), "tibia")
        write_package(client_dir, version, exe, files, "second")
        assets_name = "assets-macos.json" if os_name == "macos" else "assets.json"
        packages_builds.append({
            "os": os_name,
            "architecture": arch,
            "url": f"{cdn_url}/launcher/{client_dir_name}/package.json",
            "versionurl": f"{cdn_url}/launcher/{client_dir_name}/package.json.version",
            "assetsurl": f"{cdn_url}/launcher/assets-current/{assets_name}",
            "assetsversionurl": f"{cdn_url}/launcher/assets-current/assets.json.sha256",
        })

    metadata = {
        "launcher": {"name": "LAUNCHER", "builds": launcher_builds},
        "packages": [{
            "name": CLIENT_PACKAGE_DIR,
            "variant": "regular",
            "directoryname": CLIENT_PACKAGE_DIR,
            "logolinktarget": "https://www.idleot.com/",
            "installautomatically": True,
            "builds": packages_builds,
        }],
        "hints": f"{cdn_url}/hints.json",
    }
    write_json(out / "launcher" / "tibiametadata.json", metadata)
    # Prefer release-vendored tips (idleot/release CI has no otclient/ tree), then
    # monorepo apps/otclient/data, then whatever --previous already put in out/.
    release_root = Path(__file__).resolve().parents[1]
    hint_candidates = (
        release_root / "vendor" / "launcher-hints" / "hints.json",
        Path(__file__).resolve().parents[2] / "otclient" / "data" / "hints.json",
    )
    hints = out / "hints.json"
    vendored_hints = next((p for p in hint_candidates if p.is_file()), None)
    if vendored_hints is not None:
        hints.write_bytes(vendored_hints.read_bytes())
    elif not hints.is_file():
        write_json(hints, {"hints": ["Welcome to IdleOT."]})
    # CIP tip art: copy the whole images/ tree (news, mmorpg, …) when vendored.
    image_roots = (
        release_root / "vendor" / "launcher-hints" / "images",
        Path(__file__).resolve().parents[2] / "otclient" / "data" / "images",
    )
    for images_src in image_roots:
        if not images_src.is_dir():
            continue
        for path in images_src.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(images_src)
            # Skip client UI chrome under data/images — only tip paths (news/mmorpg/…).
            if images_src.name == "images" and "otclient" in str(images_src) and rel.parts[0] not in (
                "news",
                "mmorpg",
            ):
                continue
            target = out / "images" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size != path.stat().st_size:
                shutil.copy2(path, target)
        break
    # /play CSS backdrop + CIP launcher chrome (IdleOT replacements).
    launcher_img_roots = (
        release_root / "vendor" / "launcher-hints" / "images" / "launcher",
        Path(__file__).resolve().parents[2]
        / "otclient"
        / "src"
        / "tibia"
        / "launcher"
        / "images"
        / "launcher",
    )
    for chrome in launcher_img_roots:
        if not chrome.is_dir():
            continue
        for path in chrome.iterdir():
            if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            target = out / "images" / "launcher" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size != path.stat().st_size:
                shutil.copy2(path, target)
        break

    # /download reads latest/desktop.json — ship real launcher archives, not CIP metadata.
    # macOS must be a DMG (or ditto zip) so Qt framework symlinks survive; Python
    # zipfile flattens them and Gatekeeper then reports IdleOT.app as "damaged".
    platforms_out: dict = {}
    for key, plat in DESKTOP_JSON_KEYS.items():
        archive_dir = out / "launcher"
        archive_dir.mkdir(parents=True, exist_ok=True)
        fmt = "zip"
        archive = archive_dir / f"IdleOT-{plat}.zip"
        prebuilt_dmg = out / "installers" / f"{plat}.dmg"
        prebuilt_zip = out / "installers" / f"{plat}.zip"
        if plat == "macos-arm64" and prebuilt_dmg.is_file():
            archive = archive_dir / f"IdleOT-{plat}.dmg"
            shutil.copy2(prebuilt_dmg, archive)
            fmt = "dmg"
        elif prebuilt_zip.is_file():
            shutil.copy2(prebuilt_zip, archive)
        else:
            tree = out / "installers" / plat
            if not tree.is_dir():
                print(f"[assemble] warning: no installer for {plat} — omitted from desktop.json")
                continue
            if plat == "macos-arm64":
                app = tree / "IdleOT.app"
                if not app.is_dir():
                    print(f"[assemble] warning: no IdleOT.app under {tree} — omitted")
                    continue
                # ditto keeps framework symlinks; never use zipfile on .app trees.
                subprocess.run(
                    ["ditto", "-c", "-k", "--keepParent", str(app), str(archive)],
                    check=True,
                )
            else:
                with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for path in sorted(tree.rglob("*")):
                        if path.is_file():
                            zf.write(path, path.relative_to(tree).as_posix())
        data = archive.read_bytes()
        platforms_out[key] = {
            "url": f"launcher/{archive.name}",
            "size": len(data),
            "sha256": sha256_bytes(data),
            "file": archive.name,
            "format": fmt,
        }
    if platforms_out:
        write_json(out / "latest" / "desktop.json", {
            "schema": 2,
            "kind": "launcher",
            "revision": launcher_version,
            "version": launcher_version,
            "platforms": platforms_out,
        })
    else:
        print("[assemble] warning: desktop.json not written — no launcher installers", file=sys.stderr)
    print(f"[assemble] CIP launcher tree at {cdn_url}/launcher/tibiametadata.json")
    return True
