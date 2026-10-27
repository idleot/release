#!/usr/bin/env python3
"""Resolve what a release ships from the private umbrella repo (idleot/idleot).

The umbrella tag pins every engine via submodule pointers. A component is
selected when its pointer changed since the previous v* tag, or when forced by
COMPONENTS / a `[release:all]` flag in the tag annotation. The Tauri launcher lived in the portal repo and shipped when
apps/launcher/package.json "version" changed. The Qt CIP launcher ships with
the client; when that package.json is gone, the launcher component is skipped.

Env:
  GH_TOKEN     token that can read idleot/idleot (and idleot/portal contents)
  OWNER        GitHub org (idleot)
  REF          umbrella tag or commit
  COMPONENTS        auto | all | client | server | portal | launcher | server+portal | assets
  SKIP_BUILD        true to redeploy existing images only
  BUILD_IF_MISSING  true to compile when GHCR tags are absent (default false —
                    release fails fast; warm builds should have pushed :<sha>)

Writes key=value lines to $GITHUB_OUTPUT (and stdout).
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
ENGINES = {"client": "apps/otclient", "server": "apps/crystalserver", "portal": "apps/portal"}
COMPONENTS = [*ENGINES, "launcher"]
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def gh(path: str):
    req = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise SystemExit(f"[resolve] GitHub API {exc.code} for {path}")


def pins(repo: str, ref: str) -> dict[str, str]:
    out = {}
    for comp, path in ENGINES.items():
        entry = gh(f"/repos/{repo}/contents/{path}?ref={ref}")
        if entry and entry.get("type") == "submodule":
            out[comp] = entry["sha"]
        else:
            raise SystemExit(f"[resolve] {path} is not a submodule at {ref}")
    return out


def launcher_version(owner: str, portal_sha: str) -> str | None:
    try:
        entry = gh(f"/repos/{owner}/portal/contents/apps/launcher/package.json?ref={portal_sha}")
    except SystemExit as exc:
        print(f"::warning::cannot read the launcher version ({exc}) — RELEASE_TOKEN needs Contents read on portal")
        return None
    if not entry:
        return None
    return json.loads(base64.b64decode(entry["content"])).get("version")


def tag_message(repo: str, tag: str) -> str:
    ref = gh(f"/repos/{repo}/git/ref/tags/{tag}")
    if not ref or ref["object"]["type"] != "tag":
        return ""
    obj = gh(f"/repos/{repo}/git/tags/{ref['object']['sha']}")
    return (obj or {}).get("message", "")


def release_tags(repo: str) -> list[str]:
    tags, page = [], 1
    while True:
        batch = gh(f"/repos/{repo}/tags?per_page=100&page={page}") or []
        tags += [t["name"] for t in batch if TAG_RE.match(t["name"])]
        if len(batch) < 100:
            break
        page += 1
    return sorted(tags, key=lambda t: tuple(int(x) for x in TAG_RE.match(t).groups()))


def previous_tag(tags: list[str], ref: str) -> str | None:
    if ref in tags:
        idx = tags.index(ref)
        return tags[idx - 1] if idx > 0 else None
    return tags[-1] if tags else None


def main() -> int:
    owner = os.environ.get("OWNER", "idleot")
    repo = f"{owner}/idleot"
    ref = os.environ["REF"].strip()
    mode = (os.environ.get("COMPONENTS") or "auto").strip()
    skip_build = os.environ.get("SKIP_BUILD", "false").lower() == "true"
    build_if_missing = os.environ.get("BUILD_IF_MISSING", "false").lower() == "true"

    current = pins(repo, ref)
    tags = release_tags(repo)
    prev = previous_tag(tags, ref)
    message = tag_message(repo, ref) if ref in tags else ""

    tauri_version = launcher_version(owner, current["portal"])

    selected: set[str]
    if mode == "auto":
        if "[release:all]" in message or prev is None:
            selected = set(COMPONENTS)
            reason = "[release:all]" if prev else "first release"
        else:
            before = pins(repo, prev)
            selected = {c for c in ENGINES if before[c] != current[c]}
            if "portal" in selected and tauri_version:
                if tauri_version != launcher_version(owner, before["portal"]):
                    selected.add("launcher")
            reason = f"changed since {prev}"
    elif mode == "all":
        selected, reason = set(COMPONENTS), "forced all"
    elif mode == "assets":
        selected, reason = set(), "assets only"
    else:
        selected = set(mode.split("+"))
        unknown = selected - set(COMPONENTS)
        if unknown:
            raise SystemExit(f"[resolve] unknown components: {sorted(unknown)}")
        reason = "forced"

    # Qt CIP launcher ships inside the client/CDN bundle; do not build a Tauri slice.
    if not tauri_version and "launcher" in selected:
        if mode == "launcher" or (mode not in {"auto", "all", "assets"} and "launcher" in mode.split("+")):
            raise SystemExit(
                "[resolve] apps/launcher/package.json is gone — the Qt launcher ships with the client"
            )
        selected.discard("launcher")
        print("::notice::Tauri launcher removed — Qt launcher ships with the client")

    out = {
        "ref": ref,
        "previous": prev or "",
        "reason": reason,
        "otclient_sha": current["client"],
        "crystalserver_sha": current["server"],
        "portal_sha": current["portal"],
        "client": str("client" in selected).lower(),
        "server": str("server" in selected).lower(),
        "portal": str("portal" in selected).lower(),
        "launcher": str("launcher" in selected).lower(),
        "promote": str(bool(selected & {"client", "launcher"}) or mode == "assets").lower(),
        # Default (tag-release): build on the release path when images are
        # missing. Pass build_if_missing=false to require warm :<sha> tags.
        "build": str((not skip_build) and build_if_missing).lower(),
        "require_images": str((not skip_build) and (not build_if_missing)).lower(),
        "is_tag": str(ref in tags).lower(),
    }
    lines = "".join(f"{k}={v}\n" for k, v in out.items())
    sys.stdout.write(lines)
    if gh_out := os.environ.get("GITHUB_OUTPUT"):
        with open(gh_out, "a") as fh:
            fh.write(lines)
    if not selected and mode != "assets":
        print("::notice::nothing changed since the previous release — nothing to deploy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
