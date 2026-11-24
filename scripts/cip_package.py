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

# Monorepo: apps/qtclient. Standalone release checkout may still have apps/otclient alias.
_ROOT = Path(__file__).resolve().parents[2]
OTC = _ROOT / "qtclient" if (_ROOT / "qtclient").is_dir() else _ROOT / "otclient"

# Tip packages[].directoryname → packages/<name>/ on disk (also used as name).
# CipSoft: "Tibia" — packages/Tibia on Linux/Windows, packages/Tibia.app on macOS
# (the launcher adds .app there). See docs/reverse-engineering/cip-package-layout.md.
# CipSoft tip variants: regular | preview | test | beta.
# Local launcher-first play emits its own tip (apps/qtclient emit_play_tip.py).
CLIENT_PACKAGE_DIR = "IdleOT"
CLIENT_VARIANTS = {
    "regular": "IdleOT",
    "test": "IdleOTTest",  # qa
    "beta": "IdleOTBeta",  # remote development
    "preview": "IdleOTPreview",  # reserved for a future preview world
}

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
    "macos": "Contents/MacOS/IdleOT",
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


def cip_package_version(fallback: str = "0", prefer_fallback: bool = False) -> str:
    """CIP Play-button stamp: `<cip_version>.<6 hex>` (e.g. 15.33.c9e45e).

    Content-store revisions stay dated elsewhere; only the launcher-facing
    package.json.version uses this short form so it fits CIP's package chrome.

    The launcher reinstalls a package only when this string changes. With
    `prefer_fallback` (client packages) a local build stamps the tip revision —
    a content hash that changes on pack-only rebuilds — instead of qtclient's
    git HEAD, which would leave browsers running a stale tibia.wasm.
    """
    cip = "15.33"
    version_json = OTC / "cip" / "VERSION.json"
    if version_json.is_file():
        try:
            cip = json.loads(version_json.read_text()).get("cip_version") or cip
        except (OSError, ValueError):
            pass
    sha = os.environ.get("SOURCE_COMMIT", "").strip()
    if not sha and prefer_fallback and fallback and fallback != "0":
        sha = fallback.rsplit("-", 1)[-1]
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


# CipSoft asset layouts (docs/reverse-engineering/cip-package-layout.md):
# assets.json (Windows, Linux, web) installs at the package root next to bin/,
# assets-macos.json inside the bundle.
ASSET_LAYOUTS = {
    "assets.json": {"resources": "", "bin": "bin/"},
    "assets-macos.json": {"resources": "Contents/Resources/", "bin": "Contents/MacOS/"},
}


def asset_role(localfile: str) -> tuple[str, str] | None:
    """(role, name) of a things-list file: assets / sounds / fonts / rcc / minimap.

    The things list is CipSoft's macOS layout (publish_assets.sh); slices
    published before it carry data/things/<ver>/, data/sounds/<ver>/, data/fonts/.
    Either reads the same.
    """
    path = localfile.lstrip("/")
    for prefix in ("Contents/Resources/", ""):
        if prefix and not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if rest.startswith("assets/fonts/"):
            return ("fonts", rest[len("assets/fonts/"):])
        for role in ("assets", "sounds", "minimap"):
            if rest.startswith(role + "/"):
                return (role, rest[len(role) + 1:])
        if prefix:
            break
    if path.endswith("graphics_resources.rcc"):
        return ("rcc", "graphics_resources.rcc")
    parts = path.split("/")
    if len(parts) >= 4 and parts[0] == "data" and parts[1] in ("things", "sounds"):
        rest = "/".join(parts[3:])
        if parts[1] == "sounds":
            return ("sounds", rest)
        if rest.startswith("minimap/"):
            return ("minimap", rest[len("minimap/"):])
        return ("assets", rest)
    if len(parts) >= 3 and parts[:2] == ["data", "fonts"]:
        return ("fonts", "/".join(parts[2:]))
    return None


def asset_localfile(role: str, name: str, layout: dict[str, str]) -> str:
    res = layout["resources"]
    if role == "rcc":
        return layout["bin"] + name
    if role == "fonts":
        return f"{res}assets/fonts/{name}"
    return f"{res}{role}/{name}"


def write_asset_lists(assets_dir: Path, records: list[dict]) -> dict:
    """Write assets.json + assets-macos.json (+ sha256) from one record set.

    CipSoft ships its map tiles in the asset list too; ours land in Resources'
    minimap/ (the client's default tiles), the sibling of assets/. Returns the
    assets-macos.json document.
    """
    docs: dict[str, dict] = {}
    for name, layout in ASSET_LAYOUTS.items():
        files = []
        for rec in records:
            role = asset_role(str(rec.get("localfile") or ""))
            if role is None:
                raise SystemExit(f"[assemble] things file outside CipSoft's layout: {rec.get('localfile')}")
            files.append({**rec, "localfile": asset_localfile(role[0], role[1], layout)})
        docs[name] = {"files": files}
        write_json(assets_dir / name, docs[name])
    # One version for both lists (the launcher compares assets.json.sha256).
    (assets_dir / "assets.json.sha256").write_text(sha256_bytes((assets_dir / "assets.json").read_bytes()))
    return docs["assets-macos.json"]


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


# The web client package has CipSoft's Linux layout: the engine in bin/ (as
# bin/client), assets/ and sounds/ at the package root. The launcher starts
# bin/tibia.js (/play's argv[0]), and the client finds Resources from there
# (applicationDirPath/..), exactly as the Linux client does.
WEB_CLIENT_BIN = "bin"

# Tip packs ship inside the web packages as desktop has them: mods/<id>/ next to
# the binary (merge-pack-into-tip.sh / pack-tibia-wasm.sh). lib/ and native/ are
# linked into the wasm; the stamp / list files are packaging metadata.
WASM_PACK_SKIP_DIRS = {"lib", "native"}
WASM_PACK_SKIP_FILES = {".pack.stamp", ".files.manifest"}


def wasm_pack_files(wasm_dir: Path, launcher_chrome_only: bool) -> list[tuple[Path, str]]:
    """(blob, localfile) for mods/<id>/… next to the wasm binaries.

    launcher_chrome_only: just what the launcher reads (brand.json, mod.json
    for its launchersettings, images/launcher/); desktop shares one tip dir.
    """
    mods = wasm_dir / "mods"
    if not mods.is_dir():
        return []
    files: list[tuple[Path, str]] = []
    for path in sorted(p for p in mods.rglob("*") if p.is_file()):
        parts = path.relative_to(mods).parts  # (<id>, …)
        if len(parts) < 2 or parts[1] in WASM_PACK_SKIP_DIRS or path.name in WASM_PACK_SKIP_FILES:
            continue
        if launcher_chrome_only and not (
            parts[1:] in (("brand.json",), ("mod.json",)) or parts[1:3] == ("images", "launcher")
        ):
            continue
        files.append((path, path.relative_to(wasm_dir).as_posix()))
    return files


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
    if reuse_marker.is_file() and (assets_dir / "assets-macos.json").is_file():
        # Host/previous tip already has packed assets — do not re-lzma.
        print(f"[assemble] CIP assets-current reused ({reuse_marker.read_text().strip() or 'unchanged'})")
        records = (load_json(assets_dir / "assets-macos.json") or {}).get("files") or []
    else:
        records = records_from_list(out, assets_dir, things_rel)
    assets_doc = write_asset_lists(assets_dir, records)


    # CIP shows 15.33.c9e45e under Play — not a long dated/branch stamp.
    version = cip_package_version(release.get("revision") or "0", prefer_fallback=True)
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
                for src, localfile in wasm_pack_files(wasm_dir, launcher_chrome_only=True):
                    rec = pack_file(out, launcher_dir, src, localfile)
                    if rec:
                        launcher_records.append(rec)
        else:
            plat = INSTALLER_PLATFORM[os_name]
            tree = out / "installers" / plat
            if os_name == "macos" and tree.is_dir():
                # CipSoft: the launcher package directory is the bundle itself
                # (Contents/MacOS/Tibia), so pack from inside the .app.
                bundles = sorted(tree.glob("*.app"))
                tree = bundles[0] if len(bundles) == 1 else tree
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
                    # Plain copy: qtloader fetches tibia.data next to the tip.
                    copy_plain(src, client_dir / name)
                    rec = pack_file(out, client_dir, src, f"{WEB_CLIENT_BIN}/{name}")
                    if rec:
                        files.append(rec)
                for src, localfile in wasm_pack_files(wasm_dir, launcher_chrome_only=False):
                    rec = pack_file(out, client_dir, src, f"{WEB_CLIENT_BIN}/{localfile}")
                    if rec:
                        files.append(rec)
            exe = f"{WEB_CLIENT_BIN}/tibia.js"
        else:
            files.extend(records_from_list(out, client_dir, target.get("files")))
            files.extend(records_from_list(out, client_dir, target.get("content")))
            # CipSoft Play basename is `client` (packages/…/Contents/MacOS/client).
            # The macOS package directory *is* the bundle (launcher appends .app to
            # directoryname) — localfiles must be Contents/…, never IdleOT.app/….
            exe = executable_of(out, target.get("files"), "client")
            if os_name == "macos":
                if any(f.get("localfile") == "Contents/MacOS/client" for f in files):
                    exe = "Contents/MacOS/client"
                nested = [
                    f.get("localfile")
                    for f in files
                    if str(f.get("localfile") or "").startswith(("IdleOT.app/", "Tibia.app/", "client.app/"))
                ]
                if nested:
                    raise SystemExit(
                        f"[assemble] macOS client package has nested .app localfiles "
                        f"(e.g. {nested[0]}) — engine list must start at Contents/ "
                        f"(packages/IdleOT.app is the bundle; see cip-package-layout.md)"
                    )
                if not exe.startswith("Contents/MacOS/"):
                    raise SystemExit(
                        f"[assemble] macOS client executable must be Contents/MacOS/… "
                        f"(got {exe!r})"
                    )
                if not any(f.get("localfile") == exe for f in files):
                    raise SystemExit(
                        f"[assemble] macOS client executable {exe!r} missing from package files"
                    )
        write_package(client_dir, version, exe, files, "second")
        assets_name = "assets-macos.json" if os_name == "macos" else "assets.json"
        build = {
            "os": os_name,
            "architecture": arch,
            "url": f"{cdn_url}/launcher/{client_dir_name}/package.json",
            "versionurl": f"{cdn_url}/launcher/{client_dir_name}/package.json.version",
            "assetsurl": f"{cdn_url}/launcher/assets-current/{assets_name}",
            "assetsversionurl": f"{cdn_url}/launcher/assets-current/assets.json.sha256",
        }
        # Non-CIP channel "mods" (enabled by the pack's launcher.json "channels").
        if (out / "mods-current" / "mods.json").is_file():
            build["modsurl"] = f"{cdn_url}/launcher/mods-current/mods.json"
            build["modsversionurl"] = f"{cdn_url}/launcher/mods-current/mods.json.sha256"
        elif (out / "mods.json").is_file():
            build["modsurl"] = f"{cdn_url}/launcher/mods.json"
            build["modsversionurl"] = f"{cdn_url}/launcher/mods.json.sha256"
        packages_builds.append(build)

    # IDLEOT_TIP_VARIANT selects which CipSoft variant this CDN tip advertises
    # as the installable package (production=regular, qa=test, remote-dev=beta).
    # Builds still point at *-current / assets-current (shared content pool);
    # directoryname isolates installs under packages/<IdleOT*.app>/.
    tip_variant = (os.environ.get("IDLEOT_TIP_VARIANT") or "regular").strip().lower()
    if tip_variant not in CLIENT_VARIANTS:
        print(f"[assemble] warning: unknown IDLEOT_TIP_VARIANT={tip_variant!r} — using regular")
        tip_variant = "regular"
    tip_dir = CLIENT_VARIANTS[tip_variant]
    packages = [{
        "name": tip_dir,
        "variant": tip_variant,
        "directoryname": tip_dir,
        "logolinktarget": "https://www.idleot.com/",
        "installautomatically": True,
        "builds": packages_builds,
    }]
    # Reserved preview stub (CipSoft tip enum) until a preview world ships tips.
    if tip_variant != "preview":
        packages.append({
            "name": CLIENT_VARIANTS["preview"],
            "variant": "preview",
            "directoryname": CLIENT_VARIANTS["preview"],
            "logolinktarget": "https://www.idleot.com/",
            "installautomatically": False,
            "builds": [],
        })
    metadata = {
        "launcher": {"name": "LAUNCHER", "builds": launcher_builds},
        "packages": packages,
        "hints": f"{cdn_url}/hints.json",
    }
    write_json(out / "launcher" / "tibiametadata.json", metadata)
    print(f"[assemble] tip variant={tip_variant} directoryname={tip_dir}")
    # Prefer release-vendored tips (idleot/release CI has no otclient/ tree), then
    # monorepo apps/qtclient/data, then whatever --previous already put in out/.
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
    # /play + login splash + launcher chrome at tip /images/launcher/.
    # Prefer product pack art (IdleOT), then release-vendored tips, then CIP carve.
    # Unchanged tip keeps CIP BackgroundImage so stock qtclient stays Tibia-looking.
    # parents[2] = apps/ in the umbrella checkout; release CI sets IDLEOT_CLIENT_LAUNCHER_IMAGES.
    apps_root = Path(__file__).resolve().parents[2]
    env_chrome = os.environ.get("IDLEOT_CLIENT_LAUNCHER_IMAGES", "").strip()
    launcher_img_roots = (
        Path(env_chrome) if env_chrome else None,
        apps_root / "idleot-client" / "launcher" / "images" / "launcher",
        release_root / "vendor" / "launcher-hints" / "images" / "launcher",
        apps_root / "qtclient" / "launcher" / "upstream" / "images" / "launcher",
        apps_root / "otclient" / "src" / "tibia" / "launcher" / "images" / "launcher",
    )
    def _copy_launcher_chrome(src: Path) -> int:
        n = 0
        if not src.is_dir():
            return n
        for path in src.iterdir():
            if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            target = out / "images" / "launcher" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size != path.stat().st_size:
                shutil.copy2(path, target)
            n += 1
        return n

    for chrome in launcher_img_roots:
        if chrome is None:
            continue
        if _copy_launcher_chrome(chrome):
            print(f"[assemble] tip images/launcher ← {chrome}")
            break
    # Overlay pack tip chrome (branded wasm/desktop already merged mods/<id>/images/launcher).
    for pack_chrome in sorted(out.glob("**/mods/*/images/launcher")):
        if _copy_launcher_chrome(pack_chrome):
            print(f"[assemble] tip images/launcher ← pack {pack_chrome}")

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
