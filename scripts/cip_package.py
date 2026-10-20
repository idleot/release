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
import zipfile
from pathlib import Path

# (os, architecture field, v3 engine target or "web", launcher dir, client dir)
PLATFORMS = (
    ("windows", "x86_64", "windows-x64", "launcher-windows-current", "tibiaclient-windows-current"),
    ("macos", "x86_64;arm64", "macos-arm64", "launcher-macos-current", "tibiaclient-macos-current"),
    ("linux", "x86_64", "linux-x64", "launcher-linux-current", "tibiaclient-linux-current"),
    ("web", "wasm", "web", "launcher-web-current", "tibiaclient-web-current"),
)

DESKTOP_JSON_KEYS = {
    "windows-x64": "launcher-windows-current",
    "macos-arm64": "launcher-macos-current",
    "linux-x64": "launcher-linux-current",
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


def pack_file(out: Path, package_dir: Path, blob: Path, localfile: str) -> dict | None:
    """lzma-compress one unpacked blob. Returns a CIP file record, or None if missing."""
    if not blob.is_file():
        return None
    data = blob.read_bytes()
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
        "unpackedhash": sha256_bytes(data),
        "unpackedsize": len(data),
    }


def records_from_list(out: Path, package_dir: Path, list_rel: str | None) -> list[dict]:
    if not list_rel:
        return []
    doc = load_json(out / list_rel)
    if not doc:
        return []
    records: list[dict] = []
    files = doc.get("files", {})
    for index, (path, entry) in enumerate(files.items(), start=1):
        if index == 1 or index % 500 == 0 or index == len(files):
            print(f"[assemble] CIP {package_dir.name} {index}/{len(files)} {path}")
        digest = entry.get("sha256", "")
        blob = out / "content" / digest[:2] / digest
        rec = pack_file(out, package_dir, blob, path.lstrip("/"))
        if rec:
            records.append(rec)
        else:
            print(f"[assemble] warning: CIP package missing blob {path}")
    return records


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
    asset_files = records_from_list(out, assets_dir, things_rel)
    assets_doc = {"files": asset_files}
    write_json(assets_dir / "assets.json", assets_doc)
    assets_bytes = (assets_dir / "assets.json").read_bytes()
    (assets_dir / "assets.json.sha256").write_text(sha256_bytes(assets_bytes))
    write_json(assets_dir / "assets-macos.json", assets_doc)

    version = release.get("revision") or "0"
    launcher_builds = []
    packages_builds = []
    for os_name, arch, target_name, launcher_dir_name, client_dir_name in PLATFORMS:
        launcher_dir = out / "launcher" / launcher_dir_name
        client_dir = out / "launcher" / client_dir_name
        launcher_records: list[dict] = []
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
        if os_name != "web":
            for path in launcher_files or []:
                if path.is_file():
                    rec = pack_file(out, launcher_dir, path, path.name)
                    if rec:
                        launcher_records.append(rec)
        write_package(launcher_dir, launcher_version, "launcher", launcher_records, None)
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
                for name in ("tibia.js", "tibia.wasm", "qtloader.js"):
                    src = wasm_dir / name
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
            "name": "Tibia",
            "variant": "regular",
            "directoryname": "Tibia",
            "logolinktarget": "https://www.idleot.com/",
            "installautomatically": True,
            "builds": packages_builds,
        }],
        "hints": f"{cdn_url}/hints.json",
    }
    write_json(out / "launcher" / "tibiametadata.json", metadata)
    hints = out / "hints.json"
    vendored = Path(__file__).resolve().parents[2] / "otclient" / "data" / "hints.json"
    if vendored.is_file():
        hints.write_bytes(vendored.read_bytes())
    elif not hints.is_file():
        write_json(hints, {"hints": ["Welcome to IdleOT."]})
    news = vendored.parent / "images" / "news"
    if news.is_dir():
        dest = out / "images" / "news"
        dest.mkdir(parents=True, exist_ok=True)
        for image in news.iterdir():
            if image.is_file():
                target = dest / image.name
                if not target.exists():
                    shutil.copy2(image, target)

    platforms_out: dict = {}
    for key, dirname in DESKTOP_JSON_KEYS.items():
        folder = out / "launcher" / dirname
        archive = out / "launcher" / f"{dirname}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(folder).as_posix())
        data = archive.read_bytes()
        platforms_out[key] = {
            "url": f"launcher/{dirname}.zip",
            "size": len(data),
            "sha256": sha256_bytes(data),
            "file": archive.name,
            "format": "zip",
        }
    write_json(out / "latest" / "desktop.json", {
        "schema": 2,
        "kind": "launcher",
        "revision": launcher_version,
        "version": launcher_version,
        "platforms": platforms_out,
    })
    print(f"[assemble] CIP launcher tree at {cdn_url}/launcher/tibiametadata.json")
    return True
