#!/usr/bin/env python3
"""Pack CIP assets-current + content/ from a local assets slice (no full assemble).

Use after `make extract` + `make -C apps/otclient publish-assets ARGS=things` (or
this script's --publish). Then push with:

  CDN_SSH_HOST=… CDN_SSH_KEY=… bash apps/release/scripts/sync-cdn.sh blobs <site>

CI assemble will reuse host assets-current when assetsRevision is unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OTC = ROOT / "apps" / "otclient"
sys.path.insert(0, str(HERE))
from cip_package import emit_cip, load_json, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--slice",
        type=Path,
        default=Path(os.environ.get("IDLEOT_ASSETS_SLICE", ROOT / "dist" / "slices" / "assets")),
        help="cdn-assets slice (content/ + .idleot/v3/content-things.json)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist" / "cdn-assets-site",
        help="site tree to sync (content/ + launcher/assets-current/)",
    )
    ap.add_argument(
        "--cdn-url",
        default=os.environ.get("IDLEOT_CDN_URL", "https://static.idleot.com"),
    )
    ap.add_argument(
        "--publish",
        action="store_true",
        help="run publish_assets.sh things first into --slice",
    )
    ap.add_argument(
        "--push",
        action="store_true",
        help="sync-cdn.sh blobs after packing (needs CDN_SSH_HOST + CDN_SSH_KEY)",
    )
    args = ap.parse_args()

    if args.publish:
        env = os.environ.copy()
        env["IDLEOT_ASSETS_SLICE"] = str(args.slice.resolve())
        env.setdefault("CLIENT_VERSION", "1533")
        subprocess.check_call(
            ["bash", str(OTC / "tools" / "publish" / "publish_assets.sh"), "things"],
            cwd=ROOT,
            env=env,
        )

    slice_dir = args.slice.resolve()
    things = slice_dir / ".idleot" / "v3" / "content-things.json"
    if not things.is_file():
        print(f"[pack-assets] missing {things} — run with --publish or make publish-assets ARGS=things", file=sys.stderr)
        return 1

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # Minimal release.json so emit_cip writes assets-current.
    doc = json.loads(things.read_text())
    rel = f"releases/things-{doc['revision']}/content-things.json"
    dest_list = out / rel
    dest_list.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(things, dest_list)
    if (slice_dir / "content").is_dir():
        shutil.copytree(slice_dir / "content", out / "content", dirs_exist_ok=True)
    if (slice_dir / "packs").is_dir():
        shutil.copytree(slice_dir / "packs", out / "packs", dirs_exist_ok=True)

    release = {
        "schema": 3,
        "revision": doc.get("revision", "assets"),
        "targets": {
            "desktop": {"engine": str(doc.get("engine", "1533")), "things": rel},
            "web": {"engine": str(doc.get("engine", "1533")), "things": rel},
        },
    }
    write_json(out / "latest" / "release.json", release)
    assets_meta = load_json(slice_dir / ".idleot" / "assets.json") or {
        "assetsRevision": doc.get("revision", ""),
    }
    write_json(out / "latest" / "sources.json", {"assets": assets_meta, "history": {}})

    emit_cip(out, args.cdn_url.rstrip("/"), str(doc.get("revision", "assets")), None)
    tip = out / "launcher" / "assets-current" / "assets.json.sha256"
    print(f"[pack-assets] wrote {out}")
    if tip.is_file():
        print(f"[pack-assets] assets.json.sha256={tip.read_text().strip()}")

    if args.push:
        env = os.environ.copy()
        env.setdefault("CDN_SSH_HOST", "idleot.com")
        # CDN_SSH_KEY optional — sync-cdn falls back to the local ssh agent.
        subprocess.check_call(
            ["bash", str(HERE / "sync-cdn.sh"), "blobs", str(out)],
            cwd=HERE,
            env=env,
        )
        print("[pack-assets] sync-cdn blobs ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
